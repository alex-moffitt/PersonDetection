# -*- coding: utf-8 -*-
"""Camera Feed

This Module is used for grabbing frames from RTSP streams

All Args are set using Environment variables.
Args:
    REDIS_SERVICE_HOST: The IP of the Redis server (set automatically through k8s)
    REDIS_SERVICE_PORT: The port Redis is running on (set automatically through k8s)
    CAMERA_IP: The IP address of the camera RTSP feed (actually the full URI)
    CAMERA_NAME: The name of the camera (completely abirtrary)
    FRAME_RATE: The FPS to limit capturing to (My cameras only feed at 15FPS)
"""
import cv2
import os
from time import time, sleep
from threading import Thread
import requests
from redis import Redis
import logging

logger = logging.getLogger("Camera_Feed")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)
REDIS_HOST = os.environ.get('REDIS_SERVICE_HOST')
REDIS_PORT = os.environ.get('REDIS_SERVICE_PORT')
CAMERA_ADDRESS = os.environ.get('CAMERA_IP')
CAMERA_NAME = os.environ.get('CAMERA_NAME')
FRAME_RATE = float(os.environ.get('CAPTURE_FPS'))


class RedisMessages:
    """
    A class to handle interacting with Redis
    Args:
        redis_connection: A redis connection
    """

    def __init__(self, redis_connection):
        self.redis_conection = redis_connection
        self. prev = 0
        self.time_elapsed = time()

    def build_message(self, image):
        """Builds the message to be added to redis.
        Args:
            image: the image which will be added to the message
        """
        message = {
            "original": image.tobytes(),
            "original_shape": bytes(str(image.shape), 'utf-8'),
            "camera": str.encode(CAMERA_NAME)
        }
        return message

    def detector_online(self):
        """Determines if the main detector is online.
        """
        if self.redis_conection.exists("Primary_Detector") > 0:
            return True
        return False

    def add_message(self, message):
        """Adds message to the redis stream
        if the detector is online, otherwise it
        returns False.
        """
        if self.detector_online():
            self.redis_conection.xadd("primary_detection", message)
            return True
        return False

    def rate_limiter(self):
        """Ensures we aren't grabbing more frames than
        we want.
        """
        self.time_elapsed = time() - self.prev
        if self.time_elapsed > 1. / FRAME_RATE:
            self.prev = time()
            return True
        return False


class Client(RedisMessages):
    """ Maintain live rtsp stream without buffering.
    Args:
        redis_connection: A redis connection
    """
    _stream = None
    _active = False

    def __init__(self, redis_connection):
        super().__init__(redis_connection)
        self._queue = None
        self.open_feed()

    def open_feed(self):
        """ Opens feed from RTSP stream and creates
        a thread to retrieve frames.
        """
        self._stream = cv2.VideoCapture(CAMERA_ADDRESS)
        t = Thread(target=self._update, args=())
        t.daemon = True
        t.start()
        return self

    def _update(self):
        """Retrieves frames and updates
        self._queue with the current frame
        """
        while True:
            grabbed, frame = self._stream.read()
            if not grabbed:
                self._active = False
            else:
                self._active = True
                self._queue = frame

    def read(self):
        """Returns the grabbed frame
        with the colors fixed
        """
        return self.fix_colors(self._queue)

    def fix_colors(self, frame):
        """Fixes the colors in a frame
        Args:
            frame: The CV2 frame
        """
        blue, green, red = cv2.split(frame)
        return cv2.merge([red, green, blue])

    def stream_active(self):
        return self._active


if __name__ == "__main__":
    redis_connection = Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    frame_grabber = Client(redis_connection)
    while True:
        while frame_grabber.stream_active():
            if frame_grabber.rate_limiter():
                message = frame_grabber.build_message(frame_grabber.read())
                if frame_grabber.add_message(message):
                    pass
                else:
                    logger.info("Primary detector is offline. Waiting...")
                    while not frame_grabber.detector_online():
                        logger.info(
                            "Primary detector is still offline. Waiting...")
                        sleep(15)
                    logger.info("Primary detector is back online, resuming...")
        else:
            while not frame_grabber.stream_active():
                logger.info("Waiting for stream to come online....")
                sleep(3)
            logger.info("Stream is now online.")
