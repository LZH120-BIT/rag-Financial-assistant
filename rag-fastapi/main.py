from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import init_mongodb, init_redis, init_milvus, init_beanie_models
from app.middleware.response import ResponseWrapperMiddleware
from app.middleware.exception import (
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler,
)
from app.user.models import User
from app.user.router import router as user_router
from app.file.models import FileDocument
from app.file.router import router as file_router
from app.chat.router import router as chat_router
from app.chat.models import ChatData


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[LOG] Starting Nest application...")
    print("[LOG] MongooseModule dependencies initialized")
    print("[LOG] RedisModule dependencies initialized")
    app.state.mongo_client = await init_mongodb(settings.MONGODB_URI)
    app.state.redis = await init_redis(settings.REDIS_HOST, settings.REDIS_PORT)
    await init_beanie_models(app.state.mongo_client, "rag_fastapi_db", [User, FileDocument, ChatData])
    init_milvus(settings.MILVUS_ADDRESS)
    print("[LOG] UserinfoModule dependencies initialized")
    print("[LOG] FileanagementModule dependencies initialized")
    print("[LOG] ChatModule dependencies initialized")
    print("[LOG] Mapped {/userinfo/registeruser, POST} route")
    print("[LOG] Mapped {/userinfo/loginuser, POST} route")
    print("[LOG] Mapped {/chat/sendmessage, POST} route")
    print("[LOG] Mapped {/chat/getchatlist, GET} route")
    print("[LOG] Mapped {/chat/singlechatdata, GET} route")
    print("[LOG] Mapped {/chat/stopoutput, GET} route")
    print("[LOG] Mapped {/chat/deletechat, POST} route")
    print("[LOG] Mapped {/fileanagement/uploadkb, POST} route")
    print("[LOG] Mapped {/fileanagement/uploaddialog, POST} route")
    print("[LOG] Mapped {/fileanagement/uploadweb, POST} route")
    print("[LOG] Mapped {/fileanagement/uploadreport, POST} route")
    print("[LOG] Mapped {/fileanagement/deletefilekb, POST} route")
    print("[LOG] Mapped {/fileanagement/deletefile, POST} route")
    print("[LOG] Mapped {/fileanagement/kbfilelist, GET} route")
    print("[LOG] Nest application successfully started")
    yield
    app.state.mongo_client.close()
    await app.state.redis.close()


app = FastAPI(title="Financial Report RAG API", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ResponseWrapperMiddleware)

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

app.include_router(user_router)
app.include_router(file_router)
app.include_router(chat_router)


@app.get("/")
async def root():
    return {"message": "SUCCESS", "result": "Financial Report RAG API is running"}