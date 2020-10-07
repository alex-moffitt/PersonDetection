# Person Detection Microservice
## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [Usage](#usage)


## <a name="overview"></a> Overview

This is code is intended to run in Kubernetes on a Raspberry Pi 4. There are 3 folders which contain separate components for the main application.
- messengers
    + Send messages to slack
- peopledetector
    + Detects people in captured frames
    + Draws the boxes around the detections.
- camerafeed
    + Pulls frames from an RTSP stream using OpenCV

## <a name="features"></a> Features

This is designed to run on Kubernetes, and is able to take advantages of a microservices architecture. All settings are configured using the envrionment variables configured with Kubernetes.

Detection is done using the Coral Edge TPU.

## <a name="usage"></a> Usage
I have not included my Dockerfiles, so you will need to create docker containers for each of the applications. Kubernetes deployment Yamls are included, and are being used in my home Gitlab CI/CD pipelines.

A Redis server must be setup along with a service which can be accessed by the pods. My Redis server is setup as a basic service, and cannot be accessed outside of the cluster.

The model is trained on 640x360 images as this is the dimensions of the feed coming from the RTSP feed.

When deploying the camera feeds, adjust the deployment Yaml to match your cameras, at a minimum you will need to change the number of deployments to match your number of cameras, as well as modify the CAMERA_IP to match the full RTSP URL of the camera.

The main detection deployment has configuration to change box color scores, depending on the model you are using you might want to adjust the scores to match your needs.