FROM python:3

WORKDIR /main

COPY . .

RUN pip install flask requests

CMD ["python3", "./server.py"]

EXPOSE 8090