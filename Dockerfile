# FROM docker.xuanyuan.me/ubuntu:20.04

# ARG DEBIAN_FRONTEND=noninteractive
# ARG UID=1000
# ARG USERNAME=annotator

# # 基础依赖 + Python3/pip
# RUN apt-get update \
#  && apt-get install -y --no-install-recommends \
#       python3 python3-pip python3-venv ca-certificates curl build-essential \
#  && rm -rf /var/lib/apt/lists/*

# # 创建非 root 用户（UID 可在 build 时传入以匹配宿主用户）
# RUN useradd -m -u ${UID} ${USERNAME} || true
# WORKDIR /app

# # 先装依赖再拷贝代码，利用缓存
# COPY ./requirements.txt /app/requirements.txt
# RUN python3 -m pip install --no-cache-dir --upgrade pip \
#  && python3 -m pip install flask trueskill \
#  && python3 -m pip install --no-cache-dir -r /app/requirements.txt

# # 目录权限归非 root 用户
# RUN chown -R ${USERNAME}:${USERNAME} /app

# # 切换为非 root
# USER ${USERNAME}

# # 暴露应用端口
# EXPOSE 5000

# # 以 Gunicorn 启动 Flask 应用（模块:app）
# CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app", "--workers", "3", "--threads", "2", "--timeout", "120"]

FROM docker.xuanyuan.me/python:3.11-slim

WORKDIR /app

# 先安装依赖
COPY requirements.txt /app/
#COPY ./groups_image-ranker /app/
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install flask trueskill && \
    pip install gunicorn

# 不 COPY 源码！因为运行时会挂载本地目录
# CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]
CMD ["gunicorn", "-b", "127.0.0.1:5000", "app:app", "--workers", "4"]
