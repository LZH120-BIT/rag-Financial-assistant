from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    MONGODB_URI: str
    PASSWORD_KEY: str
    JWT_SECRET: str
    MILVUS_ADDRESS: str
    TONGYI_AKI_KEY: str
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()