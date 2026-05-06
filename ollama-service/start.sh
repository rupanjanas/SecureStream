#!/bin/bash

ollama serve &

sleep 15

ollama pull mistral:7b-instruct-q4_0
ollama pull nomic-embed-text

wait