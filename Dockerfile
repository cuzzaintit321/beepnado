FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10002 -m -s /usr/sbin/nologin beepnado && mkdir -p /data && chown beepnado:beepnado /data
COPY --chown=beepnado:beepnado app ./app
USER beepnado
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1"]
