FROM python:3.11-slim

WORKDIR /app

COPY etl/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt



# default: run all
CMD ["bash", "-lc", ""]
