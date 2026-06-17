docker build -t py-server .
docker run -d --name py-server --network supertokens-network --env-file .env -p 13245:13245 py-server