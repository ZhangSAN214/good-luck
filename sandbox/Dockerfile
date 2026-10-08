# Roundtable 代码运行沙箱（docker 后端，可选）。默认的 wasm 后端不需要 Docker。
#   docker build -t roundtable-sandbox:1 sandbox/
# 运行参数由 src/roundtable/core/tools/sandbox.py 决定：--network none、只读根目录、
# 内存与进程数限制、去掉全部权限、非 root 用户，只挂载本次的 in/（只读）、out/ 与 main.py。
FROM python:3.12.8-slim-bookworm

RUN pip install --no-cache-dir \
        numpy==2.2.1 pandas==2.2.3 scipy==1.14.1 sympy==1.13.3 matplotlib==3.10.0 \
        seaborn==0.13.2 networkx==3.4.2 pillow==11.1.0 lxml==5.3.0 \
        openpyxl==3.1.5 python-docx==1.1.2 python-pptx==1.0.2 XlsxWriter==3.2.0 fpdf2==2.8.2 \
    && useradd --uid 65534 --no-create-home --shell /usr/sbin/nologin sandbox || true

ENV MPLBACKEND=Agg PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /work
USER 65534:65534
