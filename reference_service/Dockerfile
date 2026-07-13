FROM python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup -S -g 10001 webjam && adduser -S -u 10001 -G webjam -H -h /app webjam
WORKDIR /app
COPY --chown=webjam:webjam webjam_reference/ ./webjam_reference/

USER webjam:webjam
EXPOSE 47131/tcp 47132/udp 47133/tcp

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import json,os,urllib.request; response=urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WEBJAM_HTTP_PORT','47133')+'/healthz',timeout=2); assert response.status == 200 and json.load(response)['status'] == 'ok'"

CMD ["python", "-m", "webjam_reference"]
