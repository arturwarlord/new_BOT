FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PYTHONUNBUFFERED=1
# state/ and logs/ should be volumes so the paper account / trader state survive restarts
VOLUME ["/app/state", "/app/logs", "/app/data"]
ENTRYPOINT ["python", "trade.py", "--loop"]
CMD ["--mode", "paper"]
