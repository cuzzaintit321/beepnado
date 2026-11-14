"""Dedicated browser worker. Run only behind an independently enforced egress policy."""
import asyncio
import base64
import io
import json
import os
import re
import urllib.request
from urllib.parse import urlsplit
from PIL import Image
from playwright.async_api import async_playwright

BASE=os.environ.get('BROWSER_API_URL','').rstrip('/')
TOKEN=os.environ.get('BROWSER_WORKER_TOKEN','')
PROXY=os.environ.get('BROWSER_EGRESS_PROXY','')
ACK=os.environ.get('BROWSER_ISOLATION_VERIFIED','')
ALLOWED_ORIGINS={v.strip().lower().rstrip('/') for v in os.environ.get('BROWSER_ALLOWED_ORIGINS','').split(',') if v.strip()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def origin(url):
    try:
        u=urlsplit(url)
        if u.scheme not in ('http','https') or not u.hostname or u.username is not None or u.password is not None:
            return None
        if u.port not in (None,443 if u.scheme=='https' else 80):
            return None
        if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+',u.hostname.lower()):
            return None
        return u.scheme+'://'+u.hostname.lower()
    except ValueError:
        return None


def api(path,body):
    request=urllib.request.Request(BASE+path,data=json.dumps(body).encode(),method='POST',
        headers={'Content-Type':'application/json','X-Browser-Token':TOKEN})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=15) as response:
        return json.loads(response.read(65536))


def screenshot_evidence(raw):
    with Image.open(io.BytesIO(raw)) as im:
        im=im.convert('RGB')
        fp=list(im.resize((16,16),Image.Resampling.LANCZOS).convert('L').getdata())
        im.thumbnail((960,720))
        for max_width in (960,720,480,320):
            im.thumbnail((max_width,720))
            for quality in (55,40,25):
                buf=io.BytesIO()
                im.save(buf,format='JPEG',quality=quality,optimize=True)
                if len(buf.getvalue())<=40000:
                    return base64.b64encode(buf.getvalue()).decode(),fp
    return None,None


INIT="""(() => {
 const m={fcp:null,lcp:null,cls:0};
 Object.defineProperty(window,'__bnMetrics',{value:m});
 for (const [type,callback] of [
 ['paint',list=>{for(const e of list.getEntries())if(e.name==='first-contentful-paint')m.fcp=e.startTime}],
 ['largest-contentful-paint',list=>{for(const e of list.getEntries())m.lcp=e.startTime}],
 ['layout-shift',list=>{for(const e of list.getEntries())if(!e.hadRecentInput)m.cls+=e.value}]
 ]) {try{new PerformanceObserver(callback).observe({type,buffered:true})}catch{}}
})()"""


async def capture(job):
    out={'job_id':job['id'],'lease':job['lease'],'state':'unknown','note':'worker_error'}
    if origin(job['url']) not in ALLOWED_ORIGINS:
        return out
    try:
        async with asyncio.timeout(75):
            async with async_playwright() as p:
                # Never fall back to unsandboxed Chromium. Environment is stripped
                # of the API token. Host firewall must independently enforce egress.
                browser=await p.chromium.launch(headless=True,chromium_sandbox=True,
                    proxy={'server':PROXY,'bypass':'<-loopback>'},
                    env={'HOME':'/home/pwuser','PATH':os.environ.get('PATH','/usr/bin:/bin'),'LANG':'C.UTF-8'},
                    args=['--disable-quic','--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
                try:
                    mobile=job['viewport']=='mobile'
                    context=await browser.new_context(viewport={'width':390,'height':844} if mobile else {'width':1280,'height':720},
                        device_scale_factor=1,is_mobile=mobile,has_touch=mobile,accept_downloads=False,
                        service_workers='block',ignore_https_errors=False)
                    async def guard(route):
                        req=route.request
                        if req.method not in ('GET','HEAD') or origin(req.url) not in ALLOWED_ORIGINS:
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route('**/*',guard)
                    await context.route_web_socket('**/*',lambda ws: ws.close())
                    await context.add_init_script(INIT)
                    page=await context.new_page()
                    errors=[];failures=[]
                    page.on('pageerror',lambda e:errors.append(1) if len(errors)<10000 else None)
                    page.on('requestfailed',lambda r:failures.append(1) if len(failures)<10000 else None)
                    page.on('response',lambda r:failures.append(1) if r.status>=400 and len(failures)<10000 else None)
                    try:
                        response=await page.goto(job['url'],wait_until='load',timeout=25000)
                        if origin(page.url) not in ALLOWED_ORIGINS:
                            return out
                    except Exception:
                        out.update(note='navigation_error')
                        return out
                    await page.wait_for_timeout(2000)
                    metrics=await page.evaluate("""() => {
                      const n=performance.getEntriesByType('navigation')[0];
                      const m=window.__bnMetrics||{};
                      return {load:n?.loadEventEnd||null,fcp:m.fcp,lcp:m.lcp,cls:m.cls};
                    }""")
                    def ms(value):
                        return round(value) if isinstance(value,(int,float)) and 0<=value<=120000 else None
                    text_found=None
                    if job.get('required_text'):
                        text_found=job['required_text'].casefold() in (await page.locator('body').inner_text(timeout=3000)).casefold()
                    status=response.status if response else None
                    out.update(state='failed' if status is not None and status>=400 or text_found is False else 'observed',
                        status=status,load_ms=ms(metrics.get('load')),fcp_ms=ms(metrics.get('fcp')),lcp_ms=ms(metrics.get('lcp')),
                        cls=metrics.get('cls') if isinstance(metrics.get('cls'),(int,float)) and 0<=metrics['cls']<=100 else None,
                        js_errors=len(errors),failed_resources=len(failures),text_found=text_found,note='capture_complete')
                    try:
                        raw=await page.screenshot(type='png',full_page=False,animations='disabled',timeout=10000)
                        image,fp=screenshot_evidence(raw)
                        out.update(screenshot=image,fingerprint=fp)
                        if image is None:out['note']='capture_limited'
                    except Exception:
                        out['note']='capture_limited'
                    await context.close()
                finally:
                    await browser.close()
    except TimeoutError:
        out.update(state='unknown',note='timeout')
    except Exception:
        out.update(state='unknown',note='worker_error')
    return out


async def main():
    if os.geteuid()==0 or ACK!='yes' or origin(BASE)!=BASE or len(TOKEN)<32 or not PROXY or not ALLOWED_ORIGINS or any(origin(x)!=x for x in ALLOWED_ORIGINS):
        raise SystemExit('Non-root worker, HTTPS API, token, approved origins, egress proxy and verified isolation required')
    while True:
        try:
            job=(await asyncio.to_thread(api,'/internal/browser/claim',{})).get('job')
            if job:
                result=await capture(job)
                await asyncio.to_thread(api,'/internal/browser/result',result)
            else:
                await asyncio.sleep(15)
        except Exception as exc:
            print('Browser worker:',type(exc).__name__,flush=True)
            await asyncio.sleep(20)


if __name__=='__main__':
    asyncio.run(main())
