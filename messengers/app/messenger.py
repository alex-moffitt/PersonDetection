import os
from io import BytesIO
from redis import Redis
from PIL import Image
import requests
import numpy as np
import logging
logger = logging.getLogger("MessageQueue")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)
REDIS_HOST = os.environ.get('REDIS_SERVICE_HOST')
REDIS_PORT = os.environ.get('REDIS_SERVICE_PORT')
SLACK_TOKEN = os.environ.get('SLACK_TOKEN')
DELAY = int(os.environ.get('DELAY'))
CLIENT_NAME = os.environ.get('POD_NAME')


class RedisMessages:
    def __init__(self, redis_connection):
        self.redis_conection = redis_connection

    def _reset_id(self):
        """Ensures the stream is read from the beginning.
        Without this, messages could be orphaned.
        """
        self.redis_conection.r.xgroup_setid("messages", "messengers", 0)

    def read_stream(self):
        message = self.redis_conection.xreadgroup(
            "messengers", CLIENT_NAME, {
                "messages": ">"}, 1, block=0)
        response_list = message[0][1][0]
        msg_id = response_list[0].decode("utf-8")
        self.redis_conection.xack("messages", "messengers", msg_id)
        self.redis_conection.xdel("messages", msg_id)
        return message

    def get_message_camera(self, message):
        camera = message[0][1][0][1][b'camera'].decode("utf-8")
        return camera

    def get_image(self, message):
        img = message[0][1][0][1][b'img']
        shape = message[0][1][0][1][b'shape']
        shape = shape.decode("utf-8").strip('()')
        shape = tuple(map(int, shape.split(', ')))
        return np.asarray(bytearray(img), dtype=np.uint8).reshape(shape)

    def key_nonexistent(self, camera):
        if self.redis_conection.exists("{}_message".format(camera)) == 0:
            return True
        return False

    def create_key(self, camera):
        self.redis_conection.set("{}_message".format(camera), "exists")
        self.redis_conection.expire("{}_message".format(camera), DELAY)


class SlackUpload(RedisMessages):
    def __init__(self, redis_connection):
        super().__init__(redis_connection)

    def upload(self, image, camera):
        logger.info("Sending alert for {}".format(camera))
        self.create_key(camera)
        bytebuffer = BytesIO()
        image = Image.fromarray(image)
        image.save(bytebuffer, "JPEG")
        requests.post(
            'https://slack.com/api/files.upload',
            data={
                'token': SLACK_TOKEN,
                'channels': ["#detection"],
                'title': '{} Detected Image'.format(camera)},
            files={
                'file': (
                    '{}_detected.jpeg'.format(camera),
                    bytebuffer.getvalue(),
                    'jpeg')})


if __name__ == "__main__":
    slack = SlackUpload(Redis(host=REDIS_HOST, port=REDIS_PORT, db=0))
    while True:
        message = slack.read_stream()
        camera = slack.get_message_camera(message)
        logger.info("Person detected on {}".format(camera))
        if slack.key_nonexistent(camera):
            slack.upload(slack.get_image(message), camera)
