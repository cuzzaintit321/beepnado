FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
RUN pip install --no-cache-dir playwright==1.64.1 Pillow
WORKDIR /worker
COPY worker.py /worker/worker.py
USER pwuser
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "/worker/worker.py"]
