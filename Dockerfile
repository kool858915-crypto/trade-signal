FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY trade_core.py server.py ./
COPY static/ ./static/

ENV PORT=8765
ENV DATA_DIR=/data
VOLUME /data

EXPOSE 8765

CMD ["python", "server.py"]
