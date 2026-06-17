#!/bin/bash

echo "Step 1"
cp env.example .env
echo

echo "Step 2"
chmod +x script.sh
sh script.sh
echo

echo "Step 3"
docker compose down
docker compose up -d --build

echo "------ **** ------"