FROM 404850807717.dkr.ecr.ap-southeast-7.amazonaws.com/timoto-backend@sha256:ce6070f0e15743665387026fde24559de2af914c3126103067156e3878c79894
COPY observability.py middleware.py exceptions.py /app/apps/bikes/
LABEL timoto.observability.patch="20260916-correlation-v1"
ENV OBSERVABILITY_PATCH=20260916-correlation-v1
