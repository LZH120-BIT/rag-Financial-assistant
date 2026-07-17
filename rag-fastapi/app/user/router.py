from fastapi import APIRouter, Body, Depends
from app.user.schemas import RegisterReq, LoginReq
from app.user.service import user_service
from app.middleware.auth import get_current_user

router = APIRouter(prefix="/userinfo", tags=["user"])


@router.post("/registeruser")
async def register(body: RegisterReq):
    print("\n========================================")
    print("📝 [UserController] 收到注册请求")
    print(f"   手机号: {body.phone_number}")
    print(f"   两次密码是否一致: {'✅ 一致' if body.password == body.confirm_password else '❌ 不一致'}")

    if body.password != body.confirm_password:
        print("   ❌ 注册失败: 两次密码不一致，直接返回错误")
        print("========================================\n")
        return {"message": "两次密码输入不一致", "result": [], "code": 422}

    print("   ➡️  密码校验通过，调用 UserService.register()")
    print("========================================\n")
    return await user_service.register(body.phone_number, body.password, body.confirm_password)


@router.post("/loginuser")
async def login(body: LoginReq):
    print("\n========================================")
    print("🔑 [UserController] 收到登录请求")
    print(f"   手机号: {body.phone_number}")
    print("   ➡️  调用 UserService.login()")
    print("========================================\n")
    return await user_service.login(body.phone_number, body.password)


