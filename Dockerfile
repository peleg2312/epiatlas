FROM python:3.12-slim
# dcm2niix converts DICOM series (MRI/CT/PET straight from PACS) into NIfTI
RUN apt-get update && apt-get install -y --no-install-recommends dcm2niix && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
COPY scripts/ scripts/
ENV EPIATLAS_DATA=/data PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "epiatlas.server:app", "--host", "0.0.0.0", "--port", "8000"]
