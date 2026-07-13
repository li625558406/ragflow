from datetime import datetime, timedelta

from peewee import fn

from api.db.db_models import DB, UserLoginLog
from api.db.services.common_service import CommonService


class UserLoginLogService(CommonService):
    model = UserLoginLog

    @classmethod
    @DB.connection_context()
    def create_log(cls, user, request, login_channel="password", device_type="web", device_name=None):
        """Create a login log entry after successful login."""
        ip = ""
        user_agent = ""
        if request:
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                ip = forwarded.split(",")[0].strip()
            if not ip:
                ip = request.headers.get("X-Real-IP", "") or request.remote_addr or ""
            user_agent = (request.headers.get("User-Agent", "") or "")[:512]
        now = datetime.now()
        cls.insert(
            user_id=user.id,
            email=user.email,
            nickname=user.nickname,
            login_time=now,
            ip=ip,
            device_type=device_type,
            device_name=device_name,
            login_channel=login_channel,
            user_agent=user_agent,
            status="1",
        )

    @classmethod
    @DB.connection_context()
    def get_user_logs(cls, user_id, page=1, size=20, start_date=None, end_date=None, device_type=None):
        """Get paginated login logs for a user."""
        conditions = [cls.model.user_id == user_id, cls.model.status == "1"]
        if start_date:
            try:
                conditions.append(cls.model.login_time >= datetime.strptime(start_date, "%Y-%m-%d"))
            except ValueError:
                pass
        if end_date:
            try:
                conditions.append(cls.model.login_time <= datetime.strptime(end_date, "%Y-%m-%d 23:59:59"))
            except ValueError:
                pass
        if device_type:
            conditions.append(cls.model.device_type == device_type)

        query = (
            cls.model
            .select()
            .where(*conditions)
            .order_by(cls.model.login_time.desc())
        )
        total = query.count()
        logs = [log.to_dict() for log in query.paginate(page, size)]
        return {"logs": logs, "total": total, "page": page, "size": size}

    @classmethod
    @DB.connection_context()
    def get_user_stats(cls, user_id):
        """Get login statistics for a user."""
        total = cls.model.select().where(
            cls.model.user_id == user_id, cls.model.status == "1"
        ).count()

        seven_days_ago = datetime.now() - timedelta(days=7)
        recent_count = cls.model.select().where(
            cls.model.user_id == user_id,
            cls.model.status == "1",
            cls.model.login_time >= seven_days_ago,
        ).count()

        last_login = (
            cls.model
            .select(cls.model.login_time)
            .where(cls.model.user_id == user_id, cls.model.status == "1")
            .order_by(cls.model.login_time.desc())
            .first()
        )
        last_login_time = last_login.login_time.strftime("%Y-%m-%d %H:%M") if last_login else None

        device_row = (
            cls.model
            .select(cls.model.device_type, fn.COUNT(cls.model.id).alias("cnt"))
            .where(cls.model.user_id == user_id, cls.model.status == "1")
            .group_by(cls.model.device_type)
            .order_by(fn.COUNT(cls.model.id).desc())
            .first()
        )
        common_device = device_row.device_type if device_row else None

        return {
            "total": total,
            "recent_7d": recent_count,
            "last_login_time": last_login_time,
            "common_device": common_device,
        }

    @classmethod
    @DB.connection_context()
    def cleanup_expired(cls, days=90):
        """Delete login logs older than the specified number of days."""
        cutoff = datetime.now() - timedelta(days=days)
        return cls.model.delete().where(cls.model.login_time < cutoff).execute()
