FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Lop rieng cho thu vien: chi build lai khi requirements.txt doi
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ma nguon. Khoa API va bien bi mat truyen luc chay (vd. docker run --env-file .env), khong copy vao image.
COPY config.py main.py ./
COPY config/ config/
COPY analysis/ analysis/
COPY llm/ llm/
COPY storage/ storage/
COPY ledger/ ledger/
COPY profiling/ profiling/
COPY validators/ validators/
COPY runner/ runner/
COPY skills/ skills/
COPY report/ report/
COPY slides/ slides/

ENTRYPOINT ["python"]
CMD ["main.py"]
