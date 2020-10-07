# -*- coding: utf-8 -*-
"""Person Detector

This Module is used for detecting people from RTSP Streams

All parameters are set using Environment variables.

TODO:
Determine a better way to know which Edge TPU to use.
It seems the device can change without reason despite being
plugged into the same slot.
"""
import os
import time
from threading import Thread, Lock
from multiprocessing import Queue, Process
import logging
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from edgetpu.detection.engine import DetectionEngine
from redis import Redis

logger = logging.getLogger("PrimaryDetector")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)
REDIS_HOST = os.environ.get('REDIS_SERVICE_HOST')
REDIS_PORT = os.environ.get('REDIS_SERVICE_PORT')
CONFIDENCE_SCORE = float(os.environ.get('CONFIDENCE_SCORE'))
LOW_CONFIDENCE_SCORE = float(os.environ.get('LOW_CONFIDENCE_SCORE'))
HIGH_CONFIDENCE_SCORE = float(os.environ.get('HIGH_CONFIDENCE_SCORE'))
STREAM_THREADS = int(os.environ.get('STREAM_THREADS'))
UPDATE_THREADS = int(os.environ.get('UPDATE_THREADS'))
CLIENT_NAME = os.environ.get('POD_NAME')
DETECTION_MODEL = "/app/model/custom_model.tflite"
DETECTION_DEVICE = "/sys/bus/usb/devices/2-1"


class RedisMessages:
    """
    A class to handle interacting with Redis
    Args:
        redis_connection: A redis connection
    """

    def __init__(self, redis_connection):
        self.redis_conection = redis_connection
        self.image_queue = Queue()
        self.draw_queue = Queue()
        self._reset_id()
        self.start_stream_threads(STREAM_THREADS)

    def keep_alive(self):
        self.redis_conection.set("Primary_Detector", "Active")
        self.redis_conection.expire("Primary_Detector", 3)

    def _reset_id(self):
        """Ensures the stream is read from the beginning.
        Without this, messages could be orphaned.
        """
        self.redis_conection.xgroup_setid("primary_detection", "detector", 0)

    def start_stream_threads(self, num_threads):
        """Starts the stream threads
        Args:
            num_threads: The number of threads to spawn.
        """
        thread_list = []
        for thread_number in range(num_threads):
            thread = Thread(target=self.stream, args=[thread_number])
            thread_list.append(thread)
        for thread in thread_list:
            thread.daemon = True
            thread.start()
        return self

    def stream(self, client_num):
        """Reads the Redis stream
        This method reads the message,
        decodes it and adds it to the queue.
        """
        while True:
            try:
                read = self.redis_conection.xreadgroup(
                    "detector", CLIENT_NAME + str(client_num),
                    {"primary_detection": ">"}, 1, block=0)
                response_list = read[0][1][0]
                message_id = response_list[0].decode("utf-8")
                camera = read[0][1][0][1][b'camera']
                original = read[0][1][0][1][b'original']
                original_shape = read[0][1][0][1][b'original_shape']
                original_shape = original_shape.decode("utf-8").strip('()')
                original_shape = tuple(
                    map(int, original_shape.split(', ')))
                decoded = np.asarray(
                    bytearray(original),
                    dtype=np.uint8).reshape(original_shape)
                decoded = Image.fromarray(decoded)
                queued = {
                    "camera": camera,
                    "original": original,
                    "original_shape": bytes(str(original_shape), 'utf-8'),
                    "decoded": decoded
                }
                self.redis_conection.xack(
                    "primary_detection", "detector", message_id)
                self.redis_conection.xdel("primary_detection", message_id)
                if self.image_queue.qsize() < 50:
                    self.image_queue.put(queued)
            except redis.exceptions.ResponseError:
                logger.error(
                    "Problem with redis... Waiting for it to return.")
                self._reset_id()
                time.sleep(30)

    def add_redis_message(self, stream, message):
        """Adds a message to a Redis stream
        Args:
            stream: The stream to add the message to.
            messageL: The message to be added to the stream.
        """
        self.redis_conection.xadd(stream, message)

    def get_queue_size(self):
        """Gets the queue size for the main processing.
        """
        return self.image_queue.qsize()


class PersonDetector(RedisMessages):
    """Detects people in images and
    draws cololed boxes around them.

    Args:
        redis_connection: A redis connection
    """

    def __init__(self, redis_connection):
        super().__init__(redis_connection)
        self.start_draw_processes()
        self.start_detection_threads()

    def start_detection_threads(self):
        """Starts detection threads
        """
        thread_list = []
        for _ in range(UPDATE_THREADS):
            thread = Thread(target=self.detection_feed, args=())
            thread_list.append(thread)
        for thread in thread_list:
            thread.daemon = True
            thread.start()
        return self

    def start_draw_processes(self):
        """Starts the draw processes
        """
        number_of_processes = 5
        running_tasks = [Process(target=self.draw_feed)
                         for task in range(number_of_processes)]
        for running_task in running_tasks:
            running_task.daemon = True
            running_task.start()

    def detection_feed(self):
        """Feeds images to the detection
           engine.
        """
        logger.info("Starting the detection feed.")
        engine = DetectionEngine(DETECTION_MODEL, DETECTION_DEVICE)
        while True:
            message = self.image_queue.get()
            self.detect(message, engine)

    def draw_feed(self):
        logger.info("Starting the draw feed.")
        """Feeds images which have had positve
           detections to have boxes drawn.
        """
        while True:
            message = self.draw_queue.get()
            self.drawbox(message)

    def detect(self, message, engine):
        """Detects people in images
        Args:
            Message: The message which has the decoded image
            as well as other attributes.

            engine: The detection engine.
        """
        objs = engine.detect_with_image(message['decoded'],
                                        threshold=CONFIDENCE_SCORE,
                                        keep_aspect_ratio=True,
                                        relative_coord=False,
                                        top_k=10,
                                        resample=5)
        if objs:
            logger.info(
                "Person detected on %s", message['camera'].decode("utf-8"))
            queued = {
                "img": message['decoded'],
                "shape": message['original_shape'],
                "camera": message['camera'],
                "objs": objs
            }
            logger.info("Passing to draw")
            logger.info(self.get_queue_size())
            self.draw_queue.put(queued)

    def drawbox(self, message):
        """Draws boxes around postive detections
        Args:
            message: The message which contains the decoded image
            as well as other attributes.
        """
        lock = Lock()
        lock.acquire()
        logger.info("Person detected on %s", message['camera'].decode("utf-8"))
        img = message['img']
        draw = ImageDraw.Draw(img)
        for obj in message['objs']:
            if obj.score < LOW_CONFIDENCE_SCORE:
                line_color = (255, 0, 0)
            elif ((obj.score > LOW_CONFIDENCE_SCORE) and
                  (obj.score < HIGH_CONFIDENCE_SCORE)):
                line_color = (255, 255, 0)
            else:
                line_color = (0, 255, 0)
            box = obj.bounding_box.flatten().tolist()
            draw.rectangle(box, outline=line_color, width=1)
            logger.info(obj.score)

        stream = {
            "img": img.tobytes(),
            "shape": message['shape'],
            "camera": message['camera']
        }
        logger.info("Passing to messenger")
        logger.info("Draw queue: %s", self.draw_queue.qsize())
        RedisMessages.add_redis_message(self, "messages", stream)
        lock.release()


if __name__ == "__main__":
    logger.info("Beginning the detection stuff")
    r = Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    person_detector = PersonDetector(r)
    while True:
        person_detector.keep_alive()
        time.sleep(1)
