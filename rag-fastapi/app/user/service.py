import hashlib
import hmac
from app.config import settings
from app.user.models import User
from jose import jwt


class UserService:
    def __init__(self):
        pass

    def _hash_password(self, password: str) -> str:
        return hmac.new(
            settings.PASSWORD_KEY.encode(),
            password.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _generate_token(self, user_id: str) -> str:
        return jwt.encode({"token": user_id}, settings.JWT_SECRET, algorithm="HS256")

    async def register(self, phone_number: str, password: str, confirm_password: str):
        print("\n  ┌─ [UserService] register() 开始执行")
        print(f"  │  步骤1: 查询 MongoDB 中手机号 {phone_number} 是否已存在")

        if password != confirm_password:
            print("  │  步骤0结果: ❌ 两次密码不一致")
            print("  └─ [UserService] register() 执行完毕\n")
            return {"message": "两次密码输入不一致", "result": [], "code": 422}

        existing = await User.find_one(User.phone_number == phone_number)
        if existing:
            print("  │  步骤1结果: ❌ 手机号已存在，注册失败")
            print("  └─ [UserService] register() 执行完毕\n")
            return {"message": "该账号已存在", "result": [], "code": 422}

        print("  │  步骤1结果: ✅ 手机号未注册，可以继续注册")
        print("  │  步骤2: 使用 SHA256+密钥 对密码进行哈希加密")

        password_hash = self._hash_password(password)

        print(f"  │  步骤2结果: 密码加密完成 (hash前8位: {password_hash[:8]}...)")
        print("  │  步骤3: 将用户数据写入 MongoDB")

        await User(phone_number=phone_number, password=password_hash).insert()

        print("  │  步骤3结果: ✅ 用户注册成功，数据已存入 MongoDB")
        print("  └─ [UserService] register() 执行完毕\n")
        return {"message": "SUCCESS", "result": []}

    async def login(self, phone_number: str, password: str):
        print("\n  ┌─ [UserService] login() 开始执行")
        print("  │  步骤1: 对用户输入密码进行 SHA256 哈希加密")

        password_hash = self._hash_password(password)

        print(f"  │  步骤1结果: 加密完成 (hash前8位: {password_hash[:8]}...)")
        print(f"  │  步骤2: 查询 MongoDB，匹配手机号+密码hash")

        user = await User.find_one(
            User.phone_number == phone_number,
            User.password == password_hash,
        )

        if user:
            print("  │  步骤2结果: ✅ 用户存在，账号密码匹配成功")
            print(f"  │  步骤3: 使用用户 _id({user.id}) 生成 JWT Token")
            print("  │         JWT 有效期: 10天")

            token = self._generate_token(str(user.id))

            print(f"  │  步骤3结果: ✅ Token 生成成功 (前20位: {token[:20]}...)")
            print("  │  返回 token + 手机号 + 头像 给前端")
            print("  └─ [UserService] login() 执行完毕\n")
            return {
                "message": "SUCCESS",
                "result": {
                    "token": token,
                    "phoneNumber": user.phone_number,
                    "avatar": user.avatar,
                },
            }
        else:
            print("  │  步骤2结果: ❌ 账号或密码错误，登录失败")
            print("  └─ [UserService] login() 执行完毕\n")
            return {"message": "账号或密码错误", "result": [], "code": 422}


user_service = UserService()