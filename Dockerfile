FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY trade_core.py trade_analytics.py trade_recommend.py trade_news.py recommend_news.py app_integration.py signal_lag.py server.py market_hours.py notifier.py signal_detect.py backtest.py weekly_report.py ./
COPY static/ ./static/

ENV PORT=8765
ENV DATA_DIR=/data
VOLUME /data

EXPOSE 8765

CMD ["python", "server.py"]
