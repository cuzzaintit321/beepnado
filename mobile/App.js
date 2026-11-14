import React, {useEffect,useRef,useState} from 'react';
import {ActivityIndicator, Alert, AppState, KeyboardAvoidingView, Linking, Platform, Pressable, RefreshControl, ScrollView, StatusBar, StyleSheet, Switch, Text, TextInput, View} from 'react-native';
import {SafeAreaProvider,SafeAreaView} from 'react-native-safe-area-context';
import * as SecureStore from 'expo-secure-store';

const ROOT='https://app.beepnado.com';
const KEY='beepnado.session.v1';
const C={bg:'#101522',panel:'#1a2333',line:'#303d51',text:'#f4f5fa',muted:'#aab9cc',accent:'#ff8c70',green:'#49d88b',red:'#ff687d',amber:'#ffca72'};
const color=s=>s==='up'?C.green:s==='down'?C.red:C.amber;
const state=m=>!m.enabled?'paused':(m.display_state||m.state||'pending');
const date=v=>v?new Date(v).toLocaleString():'—';
const label=s=>String(s||'pending').replace(/_/g,' ');
async function request(path,{token,csrf,method='GET',body}={}){
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),30000);
  try {
    const res=await fetch(ROOT+path,{method,signal:controller.signal,credentials:'omit',headers:{Accept:'application/json',...(body?{'Content-Type':'application/json'}:{}),...(token?{Authorization:'Bearer '+token}:{}),...(csrf?{'X-CSRF-Token':csrf}:{})},...(body?{body:JSON.stringify(body)}:{})});
    const data=await res.json().catch(()=>({}));
    if(!res.ok){const e=new Error(typeof data.detail==='string'?data.detail:'Request failed ('+res.status+')');e.status=res.status;throw e;}
    return data;
  } finally {clearTimeout(timeout);}
}
function Button({title,onPress,disabled=false,secondary=false}){
  return <Pressable accessibilityRole="button" accessibilityState={{disabled}} disabled={disabled} onPress={onPress} style={({pressed})=>[styles.button,secondary&&styles.secondary,(pressed||disabled)&&{opacity:.55}]}><Text style={[styles.buttonText,secondary&&{color:C.text}]}>{title}</Text></Pressable>;
}
function Card({children}){return <View style={styles.card}>{children}</View>}
function Brand(){return <View style={styles.brand}><View style={styles.logo}>{[30,24,17,10].map((w,i)=><View key={i} style={{width:w,height:3,borderRadius:3,backgroundColor:'#fff',marginVertical:1,transform:[{rotate:'-9deg'}]}}/>)}</View><Text style={styles.word}>beep<Text style={{color:C.accent}}>nado.</Text></Text></View>}
function Badge({value}){return <Text style={[styles.badge,{color:color(value)}]}>● {label(value).toUpperCase()}</Text>}
function Mobile(){
 const [token,setToken]=useState(null),[user,setUser]=useState(null),[data,setData]=useState(null),[alarm,setAlarm]=useState(null);
 const [boot,setBoot]=useState(true),[busy,setBusy]=useState(false),[refreshing,setRefreshing]=useState(false),[error,setError]=useState('');
 const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[tab,setTab]=useState('Overview');
 const [selected,setSelected]=useState(null),[name,setName]=useState(''),[url,setUrl]=useState(''),[authorized,setAuthorized]=useState(false);
 const generation=useRef(0), working=useRef(false);
 const call=(path,opts={})=>request(path,{token,csrf:user?.csrf,...opts});
 async function clearSession(){generation.current++;setToken(null);setUser(null);setData(null);setAlarm(null);setSelected(null);setTab('Overview');await SecureStore.deleteItemAsync(KEY);}
 async function restore(t){
   const v=generation.current;
   const me=await request('/api/me',{token:t});
   if(!me.signed_in){await clearSession();throw new Error('Please sign in again.');}
   const [d,a]=await Promise.all([request('/api/dashboard',{token:t}),request('/api/alarm-settings',{token:t})]);
   if(v!==generation.current)return;
   setToken(t);setUser(me.user);setData(d);setAlarm(a);setError('');
 }
 useEffect(()=>{(async()=>{try{const t=await SecureStore.getItemAsync(KEY);if(t)await restore(t);}catch(e){setError(e.message);}finally{setBoot(false);}})();},[]);
 async function refresh(manual=false){
   if(!token||working.current)return;
   working.current=true;const v=generation.current;if(manual)setRefreshing(true);
   try{const [d,a]=await Promise.all([call('/api/dashboard'),call('/api/alarm-settings')]);if(v===generation.current){setData(d);setAlarm(a);setError('');}}
   catch(e){if(v===generation.current){if(e.status===401){await clearSession();setError('Session expired. Sign in again.');}else setError('Unable to refresh: '+e.message);}}
   finally{working.current=false;setRefreshing(false);}
 }
 useEffect(()=>{if(!token)return;const sub=AppState.addEventListener('change',s=>{if(s==='active')void refresh();});const id=setInterval(()=>{if(AppState.currentState==='active')void refresh();},30000);return()=>{sub.remove();clearInterval(id);};},[token,user?.csrf]);
 async function signIn(){
   if(busy)return;setBusy(true);setError('');
   try{const result=await request('/api/mobile/login',{method:'POST',body:{email:email.trim(),password}});await restore(result.token);await SecureStore.setItemAsync(KEY,result.token);setPassword('');}
   catch(e){setError(e.message);}finally{setBusy(false);}
 }
 async function action(fn){if(busy)return;setBusy(true);setError('');try{await fn();}catch(e){if(e.status===401){await clearSession();setError('Session expired. Sign in again.');}else setError(e.message);}finally{setBusy(false);}}
 async function signOut(){await action(async()=>{await call('/api/logout',{method:'POST'});await clearSession();setPassword('');});}
 async function toggleAlarm(){if(!alarm)return;await action(async()=>{const a=await call('/api/alarm-settings',{method:'PUT',body:{armed:!alarm.armed}});setAlarm(a);});}
 async function add(){await action(async()=>{await call('/api/monitors',{method:'POST',body:{name:name.trim(),url:url.trim(),authorized}});setName('');setUrl('');setAuthorized(false);setTab('Monitors');await refresh();});}
 async function check(m){await action(async()=>{await call('/api/monitors/'+m.id+'/check',{method:'POST'});await refresh();});}
 const monitors=data?.monitors||[],incidents=data?.incidents||[],notices=data?.notifications||[];
 const detail=monitors.find(m=>m.id===selected);
 const go=t=>{setSelected(null);setTab(t);};
 if(boot)return <SafeAreaView style={styles.root}><ActivityIndicator color={C.accent} size="large"/></SafeAreaView>;
 if(!user)return <SafeAreaView style={styles.root}><KeyboardAvoidingView style={{flex:1}} behavior={Platform.OS==='ios'?'padding':undefined}><ScrollView contentContainerStyle={styles.login} keyboardShouldPersistTaps="handled"><Brand/><Text style={styles.h1}>Your websites. Our radar.</Text><Text style={styles.muted}>Sign in to your existing BeepNado account.</Text><Card><Text style={styles.fieldLabel}>Email</Text><TextInput style={styles.input} value={email} onChangeText={setEmail} autoCapitalize="none" autoComplete="email" keyboardType="email-address" placeholder="you@example.com" placeholderTextColor={C.muted}/><Text style={styles.fieldLabel}>Password</Text><TextInput style={styles.input} value={password} onChangeText={setPassword} secureTextEntry autoComplete="current-password" placeholder="Password" placeholderTextColor={C.muted}/>{!!error&&<Text style={styles.error}>{error}</Text>}<Button title={busy?'Signing in…':'Sign in'} disabled={busy||!email.trim()||!password} onPress={signIn}/><Button title="Account help / registration" secondary onPress={()=>Linking.openURL(ROOT+'/?signin=1').catch(()=>setError('Unable to open website.'))}/></Card></ScrollView></KeyboardAvoidingView></SafeAreaView>;
 return <SafeAreaView style={styles.root}><StatusBar barStyle="light-content"/><View style={styles.header}><Brand/><Text style={styles.plan}>{String(user.plan).toUpperCase()}</Text></View><ScrollView refreshControl={<RefreshControl refreshing={refreshing} onRefresh={()=>refresh(true)} tintColor={C.accent}/>} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
 {!!error&&<Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
 {detail?<><Button title="‹ Back to monitors" secondary onPress={()=>setSelected(null)}/><Text style={styles.h1}>{detail.name}</Text><Card><Badge value={state(detail)}/><Text selectable style={styles.muted}>{detail.url||'Heartbeat monitor'}</Text><Text style={styles.metric}>{detail.response_ms==null?'—':detail.response_ms+' ms'}</Text><Text style={styles.muted}>Latest response · {date(detail.last_checked)}</Text><Text style={styles.body}>30-day check pass rate: {detail.success_rate_30d==null?'No samples':detail.success_rate_30d+'%'}</Text>{!!detail.error&&<Text style={styles.error}>{detail.error}</Text>}{detail.kind!=='heartbeat'&&<Button title="Check now" disabled={busy||!detail.enabled} onPress={()=>check(detail)}/>}</Card><Text style={styles.h2}>Recent checks</Text>{(data.checks||[]).filter(c=>c.monitor_id===detail.id).slice(0,20).map(c=><Card key={c.id}><Badge value={c.state}/><Text style={styles.muted}>{date(c.checked_at)} · {c.region}</Text><Text style={styles.body}>{c.http_code??'—'} · {c.response_ms==null?'—':c.response_ms+' ms'}</Text>{!!c.error&&<Text style={styles.error}>{c.error}</Text>}</Card>)}</>:<>
 {tab==='Overview'&&<><Text style={styles.kicker}>OPERATIONS / OVERVIEW</Text><Text style={styles.h1}>Your monitoring radar</Text><View style={styles.stats}>{['up','down','other'].map(s=><View key={s} style={[styles.card,{flex:1,padding:12}]}><Text style={[styles.metric,{color:color(s)}]}>{monitors.filter(m=>s==='other'?!['up','down'].includes(state(m)):state(m)===s).length}</Text><Text style={styles.muted}>{s==='other'?'Other':s==='up'?'Up':'Down'}</Text></View>)}</View>{data.primary_connectivity_unverified&&<Text style={styles.error}>Primary connectivity is unverified. Check results may be inconclusive.</Text>}<Card><View style={styles.row}><View style={{flex:1}}><Text style={styles.h2}>Outage alerts</Text><Text style={{color:alarm?.armed?C.green:C.muted,fontWeight:'700'}}>{alarm?.armed?'● ARMED':'○ DISARMED'}</Text></View><Button title={alarm?.armed?'Disarm':'Arm'} disabled={busy||!alarm} onPress={toggleAlarm}/></View><Text style={styles.muted}>{alarm?.armed?(alarm.email_ready?'Outage and recovery emails remain enabled when the app is closed.':'Armed, but server email is not configured.'):'Monitoring continues without new incident emails.'}</Text></Card><Text style={styles.h2}>Monitors · {monitors.length}/{data.limit}</Text>{monitors.slice(0,5).map(m=><Monitor key={m.id} m={m} onPress={()=>{setTab('Monitors');setSelected(m.id);}}/>)}{!monitors.length&&<Text style={styles.muted}>No monitors yet. Add your first website.</Text>}<Button title="All monitors" secondary onPress={()=>go('Monitors')}/></>}
 {tab==='Monitors'&&<><Text style={styles.h1}>Monitors</Text><Text style={styles.muted}>{monitors.length} of {data.limit} monitor slots used</Text><Button title="+ Add website" disabled={monitors.length>=data.limit} onPress={()=>go('Add')}/>{monitors.map(m=><Monitor key={m.id} m={m} onPress={()=>setSelected(m.id)}/>)}</>}
 {tab==='Add'&&<><Text style={styles.h1}>Add website</Text><Card><Text style={styles.fieldLabel}>Monitor name</Text><TextInput style={styles.input} value={name} onChangeText={setName} placeholder="My website" placeholderTextColor={C.muted}/><Text style={styles.fieldLabel}>Website URL</Text><TextInput style={styles.input} value={url} onChangeText={setUrl} placeholder="https://example.com" placeholderTextColor={C.muted} autoCapitalize="none" keyboardType="url"/><View style={styles.row}><Text style={[styles.body,{flex:1}]}>I own or have permission to monitor this website.</Text><Switch value={authorized} onValueChange={setAuthorized} trackColor={{true:C.accent}}/></View><Button title="Create monitor" disabled={busy||!authorized||name.trim().length<2||!/^https?:\/\//i.test(url.trim())} onPress={add}/></Card><Button title="Cancel" secondary onPress={()=>go('Monitors')}/></>}
 {tab==='Incidents'&&<><Text style={styles.h1}>Incident history</Text>{!incidents.length&&<Card><Text style={styles.muted}>No recorded incidents.</Text></Card>}{incidents.map(i=><Card key={i.id}><Badge value={i.resolved_at?'up':'down'}/><Text style={styles.h2}>{i.monitor_name}</Text><Text style={styles.body}>{i.summary}</Text><Text style={styles.muted}>Opened: {date(i.opened_at)}</Text><Text style={styles.muted}>Resolved: {date(i.resolved_at)}</Text>{i.opening_evidence?.error&&<Text style={styles.error}>{i.opening_evidence.error}</Text>}</Card>)}</>}
 {tab==='Alerts'&&<><Text style={styles.h1}>Alert feed</Text>{!notices.length&&<Card><Text style={styles.muted}>No notifications yet.</Text></Card>}{notices.map(n=><Card key={n.id}><Badge value={n.kind==='outage'?'down':'up'}/><Text style={styles.body}>{n.message}</Text><Text style={styles.muted}>{date(n.created_at)}</Text></Card>)}</>}
 {tab==='Account'&&<><Text style={styles.h1}>Account</Text><Card><Text style={styles.body}>{user.email}</Text><Text style={styles.muted}>Plan: {user.plan}</Text><Text style={styles.muted}>Email protection: {alarm?.armed?'Armed':'Disarmed'}</Text>{data.status_page&&<Button title="Open public status page" secondary onPress={()=>Linking.openURL(ROOT+'/s/'+encodeURIComponent(data.status_page.slug)).catch(()=>setError('Unable to open status page.'))}/>}<Button title="Sign out" disabled={busy} onPress={()=>Alert.alert('Sign out?','Server monitoring and armed alerts continue.',[{text:'Cancel',style:'cancel'},{text:'Sign out',onPress:signOut}])}/></Card></>}
 </>}
 </ScrollView><View style={styles.nav}>{['Overview','Monitors','Incidents','Alerts','Account'].map((t,i)=><Pressable key={t} accessibilityRole="tab" accessibilityState={{selected:tab===t}} onPress={()=>go(t)} style={styles.navItem}><Text style={{color:tab===t?C.accent:C.muted,fontSize:19}}>{['◉','▦','⚑','⚡','●'][i]}</Text><Text style={{color:tab===t?C.accent:C.muted,fontSize:11}}>{t}</Text></Pressable>)}</View></SafeAreaView>;
}
function Monitor({m,onPress}){return <Pressable accessibilityRole="button" onPress={onPress}><Card><View style={styles.row}><Text numberOfLines={1} style={[styles.h2,{flex:1}]}>{m.name}</Text><Badge value={state(m)}/></View><Text numberOfLines={1} style={styles.muted}>{m.url||'Heartbeat monitor'}</Text><Text style={styles.body}>{m.response_ms==null?'—':m.response_ms+' ms'} · {m.success_rate_30d==null?'No samples':m.success_rate_30d+'% check pass rate'}</Text></Card></Pressable>}
export default function App(){return <SafeAreaProvider><Mobile/></SafeAreaProvider>}
const styles=StyleSheet.create({
 root:{flex:1,backgroundColor:C.bg},header:{paddingHorizontal:18,paddingVertical:12,borderBottomWidth:1,borderColor:C.line,flexDirection:'row',justifyContent:'space-between',alignItems:'center'},brand:{flexDirection:'row',alignItems:'center',gap:8},logo:{width:40,height:40,borderRadius:12,backgroundColor:'#ff725e',alignItems:'center',justifyContent:'center'},word:{color:C.text,fontSize:26,fontWeight:'900',letterSpacing:-1.3},plan:{color:C.accent,fontWeight:'800',fontSize:12},content:{padding:16,gap:12,paddingBottom:32},login:{flexGrow:1,justifyContent:'center',padding:22,gap:16},card:{backgroundColor:C.panel,borderColor:C.line,borderWidth:1,borderRadius:16,padding:16,gap:10},h1:{color:C.text,fontSize:28,fontWeight:'800',letterSpacing:-.6},h2:{color:C.text,fontSize:17,fontWeight:'700'},kicker:{color:C.accent,fontSize:11,fontWeight:'800',letterSpacing:1.4},body:{color:C.text,fontSize:14,lineHeight:21},muted:{color:C.muted,fontSize:13,lineHeight:19},error:{color:C.red,fontSize:14,lineHeight:20},metric:{color:C.text,fontSize:30,fontWeight:'800'},stats:{flexDirection:'row',gap:8},row:{flexDirection:'row',alignItems:'center',gap:12},badge:{fontWeight:'800',fontSize:11},button:{backgroundColor:C.accent,paddingVertical:13,paddingHorizontal:19,borderRadius:10,alignItems:'center',justifyContent:'center'},secondary:{backgroundColor:'#253247',borderWidth:1,borderColor:C.line},buttonText:{color:'#211522',fontWeight:'800',fontSize:14},input:{color:C.text,backgroundColor:C.bg,borderColor:C.line,borderWidth:1,borderRadius:10,padding:12,fontSize:16},fieldLabel:{color:C.muted,fontSize:13,fontWeight:'700'},nav:{flexDirection:'row',borderTopWidth:1,borderColor:C.line,backgroundColor:C.panel,paddingVertical:8},navItem:{flex:1,alignItems:'center',gap:3,paddingVertical:5}
});
