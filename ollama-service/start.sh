#!/bin/bash

ollama serve &

sleep 15

ollama pull phi3
ollama pull nomic-embed-text

wait