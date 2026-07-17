import redis.asyncio as aioredis
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from pymilvus import connections


async def init_mongodb(uri: str) -> AsyncIOMotorClient:
    client = AsyncIOMotorClient(
        uri,
        serverSelectionTimeoutMS=1000,
        connectTimeoutMS=1000,
        socketTimeoutMS=2000,
    )
    return client


async def init_beanie_models(client: AsyncIOMotorClient, db_name: str, models: list):
    await init_beanie(database=client[db_name], document_models=models)


async def init_redis(host: str, port: int) -> aioredis.Redis:
    return aioredis.Redis(host=host, port=port, decode_responses=True)


def init_milvus(address: str):
    connections.connect(alias="default", host=address.split(":")[0], port=address.split(":")[1])