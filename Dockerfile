FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tmux procps curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server.py .
COPY lib ./lib
COPY static ./static
EXPOSE 3000
CMD ["python3","server.py"]
