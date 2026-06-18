#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import logging
from datetime import datetime

from peewee import IntegrityError

from api.db.db_models import UserToken, DB
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp, datetime_format
from api.db.services.common_service import CommonService


class UserTokenService(CommonService):
    model = UserToken

    @classmethod
    @DB.connection_context()
    def create_token(cls, user_id, device_type="web", device_name=None, token=None):
        if token is None:
            token = get_uuid()
        timestamp = current_timestamp()
        cur_datetime = datetime_format(datetime.now())
        obj = cls.model.create(
            id=get_uuid(),
            user_id=user_id,
            token=token,
            device_type=device_type,
            device_name=device_name,
            last_used_at=cur_datetime,
            create_time=timestamp,
            create_date=cur_datetime,
            update_time=timestamp,
            update_date=cur_datetime,
        )
        logging.info(f"UserToken created: user_id={user_id}, device={device_type}, name={device_name}")
        return obj

    @classmethod
    @DB.connection_context()
    def find_by_token(cls, token):
        if not token or not token.strip() or len(token.strip()) < 32:
            return None
        objs = cls.model.select().where(cls.model.token == token)
        if objs:
            # Update last_used_at
            obj = objs[0]
            obj.last_used_at = datetime_format(datetime.now())
            obj.save()
            return obj
        return None

    @classmethod
    @DB.connection_context()
    def delete_token(cls, token):
        if not token or not token.strip():
            logging.warning("delete_token called with empty token")
            return
        cls.model.delete().where(cls.model.token == token).execute()

    @classmethod
    @DB.connection_context()
    def delete_all_user_tokens(cls, user_id):
        cls.model.delete().where(cls.model.user_id == user_id).execute()

    @classmethod
    @DB.connection_context()
    def migrate_legacy_token(cls, user_id, access_token):
        if not access_token or not access_token.strip() or len(access_token.strip()) < 32:
            return
        timestamp = current_timestamp()
        cur_datetime = datetime_format(datetime.now())
        try:
            cls.model.create(
                id=get_uuid(),
                user_id=user_id,
                token=access_token,
                device_type="web",
                device_name="Legacy Token (auto-migrated)",
                last_used_at=cur_datetime,
                create_time=timestamp,
                create_date=cur_datetime,
                update_time=timestamp,
                update_date=cur_datetime,
            )
            logging.info(f"Migrated legacy token for user_id={user_id}")
        except IntegrityError:
            # Token already exists (unique constraint on token) — safe to ignore
            pass
        except Exception as e:
            logging.warning(f"Failed to migrate legacy token for user_id={user_id}: {e}")
