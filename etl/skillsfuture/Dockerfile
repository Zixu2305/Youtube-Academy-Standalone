# etl/skillsfuture/Dockerfile
FROM python:3.11-slim
WORKDIR /app

COPY etl/skillsfuture/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY pipelines/skillsfuture/ /app/pipelines/skillsfuture/

CMD ["bash", "-lc", "python /app/pipelines/skillsfuture/seed_unique_skills.py && python /app/pipelines/skillsfuture/seed_skillsfuture.py && python /app/pipelines/skillsfuture/seed_mapping.py"]
