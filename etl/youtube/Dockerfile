FROM python:3.11-slim

WORKDIR /app

COPY requirements/youtube.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY pipelines/__init__.py /app/pipelines/__init__.py
COPY pipelines/youtube/ /app/pipelines/youtube/
COPY pipelines/quiz_gen/ /app/pipelines/quiz_gen/

EXPOSE 5000

CMD ["python", "/app/pipelines/youtube/Youtube_API_Ingestion_Prototype_GUI.py"]
