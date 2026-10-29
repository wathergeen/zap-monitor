services:
  evolution-api:
    container_name: evolution-api
    hostname: evolution-api
    image: evoapicloud/evolution-api:latest
    restart: unless-stopped
    depends_on:
      - evolution-redis
      - evolution-postgres
    ports:
      - 38080:8080
    volumes:
      -  /mnt/Applications/configs/evolution-api/evolution_instances:/evolution/instances
    networks:
      - evolution-net
    env_file:
      - .env

  evolution-redis:
    container_name: evolution-redis
    hostname: evolution-redis
    image: redis:latest
    restart: unless-stopped
    networks:
      - evolution-net
    volumes:
      - /mnt/Applications/configs/evolution-api/evolution-api/redis:/data
    ports:
      - 38079:6379

  evolution-postgres:
    container_name: evolution-postgres
    hostname: evolution-postgres
    image: postgres:15
    networks:
      - evolution-net
    restart: unless-stopped
    ports:
      - 38032:5432
    environment:
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB}
    volumes:
    - /mnt/Applications/configs/evolution-api/database:/var/lib/postgresql/data

networks:
  evolution-net:
    driver: bridge