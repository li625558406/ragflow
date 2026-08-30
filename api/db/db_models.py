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
import hashlib
import inspect
import logging
import operator
import os
import sys
import time
import typing
from datetime import date, datetime, timezone
from enum import Enum
from functools import wraps

from quart_auth import AuthUser
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from peewee import (
    fn,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    BigIntegerField,
    BlobField,
    BooleanField,
    CharField,
    CompositeKey,
    DateField,
    DateTimeField,
    Field,
    FloatField,
    IntegerField,
    Metadata,
    Model,
    TextField,
    PrimaryKeyField,
)
from playhouse.migrate import MySQLMigrator, PostgresqlMigrator, migrate
from playhouse.pool import PooledMySQLDatabase, PooledPostgresqlDatabase

from api import utils
from api.db import SerializedType
from api.utils.json_encode import json_dumps, json_loads
from api.utils.configs import deserialize_b64, serialize_b64

from common.time_utils import current_timestamp, timestamp_to_date, date_string_to_timestamp
from common.decorator import singleton
from common.constants import ParserType, MAXIMUM_TASK_PAGE_NUMBER
from common import settings

from api.constants import (
    NORMAL_ROLE_NAME,
    NORMAL_ROLE_PERMISSIONS,
    SUPER_ROLE_NAME,
)


CONTINUOUS_FIELD_TYPE = {IntegerField, FloatField, DateTimeField}
AUTO_DATE_TIMESTAMP_FIELD_PREFIX = {"create", "start", "end", "update", "read_access", "write_access"}


class TextFieldType(Enum):
    MYSQL = "LONGTEXT"
    OCEANBASE = "LONGTEXT"
    POSTGRES = "TEXT"


class LongTextField(TextField):
    field_type = TextFieldType[settings.DATABASE_TYPE.upper()].value


class JSONField(LongTextField):
    default_value = {}

    def __init__(self, object_hook=None, object_pairs_hook=None, **kwargs):
        self._object_hook = object_hook
        self._object_pairs_hook = object_pairs_hook
        super().__init__(**kwargs)

    def db_value(self, value):
        if value is None:
            value = self.default_value
        return json_dumps(value)

    def python_value(self, value):
        if not value:
            return self.default_value
        try:
            return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)
        except Exception:
            logging.warning("JSONField.python_value: failed to decode JSON, returning default. raw=%s", value[:200] if value else value)
            return self.default_value


class ListField(JSONField):
    default_value = []


class SerializedField(LongTextField):
    def __init__(self, serialized_type=SerializedType.PICKLE, object_hook=None, object_pairs_hook=None, **kwargs):
        self._serialized_type = serialized_type
        self._object_hook = object_hook
        self._object_pairs_hook = object_pairs_hook
        super().__init__(**kwargs)

    def db_value(self, value):
        if self._serialized_type == SerializedType.PICKLE:
            return serialize_b64(value, to_str=True)
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return None
            return json_dumps(value, with_type=True)
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")

    def python_value(self, value):
        if self._serialized_type == SerializedType.PICKLE:
            return deserialize_b64(value)
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return {}
            return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")


def is_continuous_field(cls: typing.Type) -> bool:
    if cls in CONTINUOUS_FIELD_TYPE:
        return True
    for p in cls.__bases__:
        if p in CONTINUOUS_FIELD_TYPE:
            return True
        elif p is not Field and p is not object:
            if is_continuous_field(p):
                return True
    else:
        return False


def auto_date_timestamp_field():
    return {f"{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def auto_date_timestamp_db_field():
    return {f"f_{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def remove_field_name_prefix(field_name):
    return field_name[2:] if field_name.startswith("f_") else field_name


class BaseModel(Model):
    create_time = BigIntegerField(null=True, index=True)
    create_date = DateTimeField(null=True, index=True)
    update_time = BigIntegerField(null=True, index=True)
    update_date = DateTimeField(null=True, index=True)

    def to_json(self):
        # This function is obsolete
        return self.to_dict()

    def to_dict(self):
        return self.__dict__["__data__"]

    def to_human_model_dict(self, only_primary_with: list = None):
        model_dict = self.__dict__["__data__"]

        if not only_primary_with:
            return {remove_field_name_prefix(k): v for k, v in model_dict.items()}

        human_model_dict = {}
        for k in self._meta.primary_key.field_names:
            human_model_dict[remove_field_name_prefix(k)] = model_dict[k]
        for k in only_primary_with:
            human_model_dict[k] = model_dict[f"f_{k}"]
        return human_model_dict

    @property
    def meta(self) -> Metadata:
        return self._meta

    @classmethod
    def get_primary_keys_name(cls):
        return cls._meta.primary_key.field_names if isinstance(cls._meta.primary_key, CompositeKey) else [cls._meta.primary_key.name]

    @classmethod
    def getter_by(cls, attr):
        return operator.attrgetter(attr)(cls)

    @classmethod
    def query(cls, reverse=None, order_by=None, **kwargs):
        filters = []
        for f_n, f_v in kwargs.items():
            attr_name = "%s" % f_n
            if not hasattr(cls, attr_name) or f_v is None:
                continue
            if type(f_v) in {list, set}:
                f_v = list(f_v)
                if is_continuous_field(type(getattr(cls, attr_name))):
                    if len(f_v) == 2:
                        for i, v in enumerate(f_v):
                            if isinstance(v, str) and f_n in auto_date_timestamp_field():
                                # time type: %Y-%m-%d %H:%M:%S
                                f_v[i] = date_string_to_timestamp(v)
                        lt_value = f_v[0]
                        gt_value = f_v[1]
                        if lt_value is not None and gt_value is not None:
                            filters.append(cls.getter_by(attr_name).between(lt_value, gt_value))
                        elif lt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) >= lt_value)
                        elif gt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) <= gt_value)
                else:
                    filters.append(operator.attrgetter(attr_name)(cls) << f_v)
            else:
                filters.append(operator.attrgetter(attr_name)(cls) == f_v)
        if filters:
            query_records = cls.select().where(*filters)
            if reverse is not None:
                if not order_by or not hasattr(cls, f"{order_by}"):
                    order_by = "create_time"
                if reverse is True:
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").desc())
                elif reverse is False:
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").asc())
            return [query_record for query_record in query_records]
        else:
            return []

    @classmethod
    def insert(cls, __data=None, **insert):
        if isinstance(__data, dict) and __data:
            __data[cls._meta.combined["create_time"]] = current_timestamp()
        if insert:
            insert["create_time"] = current_timestamp()

        return super().insert(__data, **insert)

    # update and insert will call this method
    @classmethod
    def _normalize_data(cls, data, kwargs):
        normalized = super()._normalize_data(data, kwargs)
        if not normalized:
            return {}

        normalized[cls._meta.combined["update_time"]] = current_timestamp()

        for f_n in AUTO_DATE_TIMESTAMP_FIELD_PREFIX:
            if {f"{f_n}_time", f"{f_n}_date"}.issubset(cls._meta.combined.keys()) and cls._meta.combined[f"{f_n}_time"] in normalized and normalized[cls._meta.combined[f"{f_n}_time"]] is not None:
                ts_val = normalized[cls._meta.combined[f"{f_n}_time"]]
                if isinstance(ts_val, (datetime, date)):
                    normalized[cls._meta.combined[f"{f_n}_date"]] = ts_val.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts_val, datetime) else ts_val.strftime("%Y-%m-%d")
                elif isinstance(ts_val, str) and ts_val.strip():
                    # Handle datetime string (e.g. "2026-05-24 17:33:16" from external APIs)
                    try:
                        dt = datetime.strptime(ts_val.strip(), "%Y-%m-%d %H:%M:%S")
                        normalized[cls._meta.combined[f"{f_n}_date"]] = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        try:
                            dt = datetime.strptime(ts_val.strip(), "%Y-%m-%d")
                            normalized[cls._meta.combined[f"{f_n}_date"]] = dt.strftime("%Y-%m-%d")
                        except ValueError:
                            try:
                                normalized[cls._meta.combined[f"{f_n}_date"]] = timestamp_to_date(ts_val)
                            except (ValueError, TypeError):
                                pass  # non-standard format, leave _date unset
                elif ts_val:
                    # Numeric timestamp (int/float) or other truthy value
                    try:
                        normalized[cls._meta.combined[f"{f_n}_date"]] = timestamp_to_date(ts_val)
                    except (ValueError, TypeError):
                        pass  # unrecognized value, leave _date unset

        return normalized


class JsonSerializedField(SerializedField):
    def __init__(self, object_hook=utils.from_dict_hook, object_pairs_hook=None, **kwargs):
        super(JsonSerializedField, self).__init__(serialized_type=SerializedType.JSON, object_hook=object_hook, object_pairs_hook=object_pairs_hook, **kwargs)


class RetryingPooledMySQLDatabase(PooledMySQLDatabase):
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection']
                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    (str(e) in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"Database connection issue (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    logging.error(f"DB execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        # self.close_all()
        # self.connect()
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection']

                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    (str(e) in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"Lost connection during transaction (attempt {attempt+1}/{self.max_retries})"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    raise
        return None


class RetryingPooledPostgresqlDatabase(PooledPostgresqlDatabase):
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                # PostgreSQL specific error codes
                # 57P01: admin_shutdown
                # 57P02: crash_shutdown
                # 57P03: cannot_connect_now
                # 08006: connection_failure
                # 08003: connection_does_not_exist
                # 08000: connection_exception
                error_messages = ['connection', 'server closed', 'connection refused',
                                'no connection to the server', 'terminating connection']

                should_retry = any(msg in str(e).lower() for msg in error_messages)

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"PostgreSQL connection issue (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    logging.error(f"PostgreSQL execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect to PostgreSQL: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect to PostgreSQL on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_messages = ['connection', 'server closed', 'connection refused',
                                'no connection to the server', 'terminating connection']

                should_retry = any(msg in str(e).lower() for msg in error_messages)

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"PostgreSQL connection lost during transaction (attempt {attempt+1}/{self.max_retries})"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    raise
        return None


class RetryingPooledOceanBaseDatabase(PooledMySQLDatabase):
    """Pooled OceanBase database with retry mechanism.

    OceanBase is compatible with MySQL protocol, so we inherit from PooledMySQLDatabase.
    This class provides connection pooling and automatic retry for connection issues.
    """
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                # OceanBase/MySQL specific error codes
                # 2013: Lost connection to MySQL server during query
                # 2006: MySQL server has gone away
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection', 'gone away']

                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    any(msg in str(e).lower() for msg in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"OceanBase connection issue (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    logging.error(f"OceanBase execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect to OceanBase: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect to OceanBase on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection']

                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    (str(e) in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"Lost connection during transaction (attempt {attempt+1}/{self.max_retries})"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    raise
        return None


class PooledDatabase(Enum):
    MYSQL = RetryingPooledMySQLDatabase
    OCEANBASE = RetryingPooledOceanBaseDatabase
    POSTGRES = RetryingPooledPostgresqlDatabase


class DatabaseMigrator(Enum):
    MYSQL = MySQLMigrator
    OCEANBASE = MySQLMigrator
    POSTGRES = PostgresqlMigrator


@singleton
class BaseDataBase:
    def __init__(self):
        database_config = settings.DATABASE.copy()
        db_name = database_config.pop("name")

        pool_config = {
            'max_retries': 5,
            'retry_delay': 1,
        }
        database_config.update(pool_config)
        self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(
            db_name, **database_config
        )
        # self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(db_name, **database_config)
        logging.info("init database on cluster mode successfully")


def with_retry(max_retries=3, retry_delay=1.0):
    """Decorator: Add retry mechanism to database operations

    Args:
        max_retries (int): maximum number of retries
        retry_delay (float): initial retry delay (seconds), will increase exponentially

    Returns:
        decorated function
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for retry in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    # get self and method name for logging
                    self_obj = args[0] if args else None
                    func_name = func.__name__
                    lock_name = getattr(self_obj, "lock_name", "unknown") if self_obj else "unknown"

                    if retry < max_retries - 1:
                        current_delay = retry_delay * (2**retry)
                        logging.warning(f"{func_name} {lock_name} failed: {str(e)}, retrying ({retry + 1}/{max_retries})")
                        time.sleep(current_delay)
                    else:
                        logging.error(f"{func_name} {lock_name} failed after all attempts: {str(e)}")

            if last_exception:
                raise last_exception
            return False

        return wrapper

    return decorator


class PostgresDatabaseLock:
    def __init__(self, lock_name, timeout=10, db=None):
        self.lock_name = lock_name
        self.lock_id = int(hashlib.md5(lock_name.encode()).hexdigest(), 16) % (2**31 - 1)
        self.timeout = int(timeout)
        self.db = db if db else DB

    @with_retry(max_retries=3, retry_delay=1.0)
    def lock(self):
        cursor = self.db.execute_sql("SELECT pg_try_advisory_lock(%s)", (self.lock_id,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"acquire postgres lock {self.lock_name} timeout")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"failed to acquire lock {self.lock_name}")

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        cursor = self.db.execute_sql("SELECT pg_advisory_unlock(%s)", (self.lock_id,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"postgres lock {self.lock_name} was not established by this thread")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"postgres lock {self.lock_name} does not exist")

    def __enter__(self):
        if isinstance(self.db, PooledPostgresqlDatabase):
            self.lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(self.db, PooledPostgresqlDatabase):
            self.unlock()

    def __call__(self, func):
        @wraps(func)
        def magic(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return magic


class MysqlDatabaseLock:
    def __init__(self, lock_name, timeout=10, db=None):
        self.lock_name = lock_name
        self.timeout = int(timeout)
        self.db = db if db else DB

    @with_retry(max_retries=3, retry_delay=1.0)
    def lock(self):
        # SQL parameters only support %s format placeholders
        cursor = self.db.execute_sql("SELECT GET_LOCK(%s, %s)", (self.lock_name, self.timeout))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"acquire mysql lock {self.lock_name} timeout")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"failed to acquire lock {self.lock_name}")

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        cursor = self.db.execute_sql("SELECT RELEASE_LOCK(%s)", (self.lock_name,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"mysql lock {self.lock_name} was not established by this thread")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"mysql lock {self.lock_name} does not exist")

    def __enter__(self):
        if isinstance(self.db, PooledMySQLDatabase):
            self.lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(self.db, PooledMySQLDatabase):
            self.unlock()

    def __call__(self, func):
        @wraps(func)
        def magic(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return magic


class DatabaseLock(Enum):
    MYSQL = MysqlDatabaseLock
    OCEANBASE = MysqlDatabaseLock
    POSTGRES = PostgresDatabaseLock


DB = BaseDataBase().database_connection
DB.lock = DatabaseLock[settings.DATABASE_TYPE.upper()].value


def close_connection():
    try:
        if DB:
            DB.close_stale(age=30)
    except Exception as e:
        logging.exception(e)


class DataBaseModel(BaseModel):
    class Meta:
        database = DB


@DB.connection_context()
@DB.lock("init_database_tables", 60)
def init_database_tables(alter_fields=[]):
    members = inspect.getmembers(sys.modules[__name__], inspect.isclass)
    table_objs = []
    create_failed_list = []
    for name, obj in members:
        if obj != DataBaseModel and issubclass(obj, DataBaseModel):
            table_objs.append(obj)

            if not obj.table_exists():
                logging.debug(f"start create table {obj.__name__}")
                try:
                    obj.create_table(safe=True)
                    logging.debug(f"create table success: {obj.__name__}")
                except Exception as e:
                    logging.exception(e)
                    create_failed_list.append(obj.__name__)
            else:
                logging.debug(f"table {obj.__name__} already exists, skip creation.")

    if create_failed_list:
        logging.error(f"create tables failed: {create_failed_list}")
        raise Exception(f"create tables failed: {create_failed_list}")
    migrate_db()


def fill_db_model_object(model_object, human_model_dict):
    for k, v in human_model_dict.items():
        attr_name = "%s" % k
        if hasattr(model_object.__class__, attr_name):
            setattr(model_object, attr_name, v)
    return model_object


class User(DataBaseModel, AuthUser):
    id = CharField(max_length=32, primary_key=True)
    access_token = CharField(max_length=255, null=True, index=True)
    nickname = CharField(max_length=100, null=False, help_text="nicky name", index=True)
    password = CharField(max_length=255, null=True, help_text="password", index=True)
    email = CharField(max_length=255, null=False, help_text="email", unique=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    language = CharField(max_length=32, null=True, help_text="English|Chinese", default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", index=True)
    color_schema = CharField(max_length=32, null=True, help_text="Bright|Dark", default="Bright", index=True)
    timezone = CharField(max_length=64, null=True, help_text="Timezone", default="UTC+8\tAsia/Shanghai", index=True)
    last_login_time = DateTimeField(null=True, index=True)
    is_authenticated = CharField(max_length=1, null=False, default="1", index=True)
    is_active = CharField(max_length=1, null=False, default="1", index=True)
    is_anonymous = CharField(max_length=1, null=False, default="0", index=True)
    login_channel = CharField(null=True, help_text="from which user login", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)
    is_superuser = BooleanField(null=True, help_text="is root", default=False, index=True)

    def __str__(self):
        return self.email

    def get_id(self):
        jwt = Serializer(secret_key=settings.SECRET_KEY)
        return jwt.dumps(str(self.access_token))

    class Meta:
        db_table = "user"


class UserToken(DataBaseModel):
    """Multi-device token table: each device login creates a new row instead of overwriting users.access_token.
    This allows simultaneous web + mobile sessions without kicking each other out.
    """
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, index=True)
    token = CharField(max_length=255, null=False, index=True, unique=True)
    device_type = CharField(max_length=32, default="web")
    device_name = CharField(max_length=255, null=True)
    last_used_at = DateTimeField(null=True)
    create_time = BigIntegerField(null=True)
    create_date = DateTimeField(null=True)
    update_time = BigIntegerField(null=True)
    update_date = DateTimeField(null=True)

    class Meta:
        db_table = "user_token"


class Tenant(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=100, null=True, help_text="Tenant name", index=True)
    public_key = CharField(max_length=255, null=True, index=True)
    llm_id = CharField(max_length=128, null=False, help_text="default llm ID", index=True)
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    embd_id = CharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    asr_id = CharField(max_length=128, null=False, help_text="default ASR model ID", index=True)
    tenant_asr_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    img2txt_id = CharField(max_length=128, null=False, help_text="default image to text model ID", index=True)
    tenant_img2txt_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    rerank_id = CharField(max_length=128, null=False, help_text="default rerank model ID", index=True)
    tenant_rerank_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    tts_id = CharField(max_length=256, null=True, help_text="default tts model ID", index=True)
    tenant_tts_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    parser_ids = CharField(max_length=256, null=False, help_text="document processors", index=True)
    credit = IntegerField(default=512, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "tenant"


class UserTenant(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    role = CharField(max_length=32, null=False, help_text="UserTenantRole", index=True)
    invited_by = CharField(max_length=32, null=False, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "user_tenant"


class InvitationCode(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    code = CharField(max_length=32, null=False, index=True)
    visit_time = DateTimeField(null=True, index=True)
    user_id = CharField(max_length=32, null=True, index=True)
    tenant_id = CharField(max_length=32, null=True, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "invitation_code"


class LLMFactories(DataBaseModel):
    name = CharField(max_length=128, null=False, help_text="LLM factory name", primary_key=True)
    logo = TextField(null=True, help_text="llm logo base64")
    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    rank = IntegerField(default=0, index=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "llm_factories"


class LLM(DataBaseModel):
    # LLMs dictionary
    llm_name = CharField(max_length=128, null=False, help_text="LLM name", index=True)
    model_type = CharField(max_length=128, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    fid = CharField(max_length=128, null=False, help_text="LLM factory id", index=True)
    max_tokens = IntegerField(default=0)

    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, Chat, 32k...", index=True)
    is_tools = BooleanField(null=False, help_text="support tools", default=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.llm_name

    class Meta:
        primary_key = CompositeKey("fid", "llm_name")
        db_table = "llm"


class TenantLLM(DataBaseModel):
    id = PrimaryKeyField()
    tenant_id = CharField(max_length=32, null=False, index=True)
    llm_factory = CharField(max_length=128, null=False, help_text="LLM factory name", index=True)
    model_type = CharField(max_length=128, null=True, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    llm_name = CharField(max_length=128, null=True, help_text="LLM name", default="", index=True)
    api_key = TextField(null=True, help_text="API KEY")
    api_base = CharField(max_length=255, null=True, help_text="API Base")
    max_tokens = IntegerField(default=8192, help_text="Max context token num", index=True)
    used_tokens = IntegerField(default=0, help_text="Used token num", index=True)
    status = CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.llm_name

    class Meta:
        db_table = "tenant_llm"
        indexes = (
            (("tenant_id", "llm_factory", "llm_name"), True),
        )


class TenantLangfuse(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, primary_key=True)
    secret_key = CharField(max_length=2048, null=False, help_text="SECRET KEY", index=True)
    public_key = CharField(max_length=2048, null=False, help_text="PUBLIC KEY", index=True)
    host = CharField(max_length=128, null=False, help_text="HOST", index=True)

    def __str__(self):
        return "Langfuse host" + self.host

    class Meta:
        db_table = "tenant_langfuse"


class Knowledgebase(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="KB name", index=True)
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    description = TextField(null=True, help_text="KB description")
    embd_id = CharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    created_by = CharField(max_length=32, null=False, index=True)
    doc_num = IntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    similarity_threshold = FloatField(default=0.2, index=True)
    vector_similarity_weight = FloatField(default=0.3, index=True)

    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", default=ParserType.NAIVE.value, index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="Pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    pagerank = IntegerField(default=0, index=False)

    graphrag_task_id = CharField(max_length=32, null=True, help_text="Graph RAG task ID", index=True)
    graphrag_task_finish_at = DateTimeField(null=True)
    raptor_task_id = CharField(max_length=32, null=True, help_text="RAPTOR task ID", index=True)
    raptor_task_finish_at = DateTimeField(null=True)
    mindmap_task_id = CharField(max_length=32, null=True, help_text="Mindmap task ID", index=True)
    mindmap_task_finish_at = DateTimeField(null=True)

    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "knowledgebase"


class Document(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    thumbnail = TextField(null=True, help_text="thumbnail base64 string")
    kb_id = CharField(max_length=256, null=False, index=True)
    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    source_type = CharField(max_length=128, null=False, default="local", help_text="where dose this document come from", index=True)
    type = CharField(max_length=32, null=False, help_text="file extension", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=True, help_text="file name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = BigIntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    process_begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)
    suffix = CharField(max_length=32, null=False, help_text="The real file extension suffix", index=True)

    content_hash = CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True)

    run = CharField(max_length=1, null=True, help_text="start to run processing or cancel.(1: run it; 2: cancel)", default="0", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "document"


class File(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    parent_id = CharField(max_length=32, null=False, help_text="parent folder id", index=True)
    tenant_id = CharField(max_length=32, null=False, help_text="tenant id", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=False, help_text="file name or folder name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = BigIntegerField(default=0, index=True)
    type = CharField(max_length=32, null=False, help_text="file extension", index=True)
    source_type = CharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True)

    class Meta:
        db_table = "file"


class File2Document(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    file_id = CharField(max_length=32, null=True, help_text="file id", index=True)
    document_id = CharField(max_length=32, null=True, help_text="document id", index=True)

    class Meta:
        db_table = "file2document"


class Task(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    doc_id = CharField(max_length=32, null=False, index=True)
    from_page = IntegerField(default=0)
    to_page = IntegerField(default=MAXIMUM_TASK_PAGE_NUMBER)
    task_type = CharField(max_length=32, null=False, default="")
    priority = IntegerField(default=0)

    begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)

    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    retry_count = IntegerField(default=0)
    digest = TextField(null=True, help_text="task digest", default="")
    chunk_ids = LongTextField(null=True, help_text="chunk ids", default="")


class Dialog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="dialog application name", index=True)
    description = TextField(null=True, help_text="Dialog description")
    icon = TextField(null=True, help_text="icon base64 string")
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    llm_id = CharField(max_length=128, null=False, help_text="default llm ID")
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)

    llm_setting = JSONField(null=False, default={"temperature": 0.1, "top_p": 0.3, "frequency_penalty": 0.7, "presence_penalty": 0.4, "max_tokens": 512})
    prompt_type = CharField(max_length=16, null=False, default="simple", help_text="simple|advanced", index=True)
    prompt_config = JSONField(
        null=False,
        default={"system": "", "prologue": "Hi! I'm your assistant. What can I do for you?", "parameters": [], "empty_response": "Sorry! No relevant content was found in the knowledge base!"},
    )
    meta_data_filter = JSONField(null=True, default={})

    similarity_threshold = FloatField(default=0.2)
    vector_similarity_weight = FloatField(default=0.3)

    top_n = IntegerField(default=6)

    top_k = IntegerField(default=1024)

    do_refer = CharField(max_length=1, null=False, default="1", help_text="it needs to insert reference index into answer or not")

    rerank_id = CharField(max_length=128, null=False, help_text="default rerank model ID")
    tenant_rerank_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    kb_ids = JSONField(null=False, default=[])
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "dialog"


class Conversation(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    dialog_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="conversation name", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    user_id = CharField(max_length=255, null=True, help_text="user_id", index=True)

    class Meta:
        db_table = "conversation"


class APIToken(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, index=True)
    token = CharField(max_length=255, null=False, index=True)
    dialog_id = CharField(max_length=32, null=True, index=True)
    source = CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True)
    beta = CharField(max_length=255, null=True, index=True)

    class Meta:
        db_table = "api_token"
        primary_key = CompositeKey("tenant_id", "token")


class API4Conversation(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=True, help_text="conversation name", index=False)
    dialog_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=255, null=False, help_text="user_id", index=True)
    exp_user_id = CharField(max_length=255, null=True, help_text="exp_user_id", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    tokens = IntegerField(default=0)
    source = CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True)
    dsl = JSONField(null=True, default={})
    duration = FloatField(default=0, index=True)
    round = IntegerField(default=0, index=True)
    thumb_up = IntegerField(default=0, index=True)
    errors = TextField(null=True, help_text="errors")
    version_title = CharField(max_length=255, null=True, help_text="canvas version title when session created", index=False)

    class Meta:
        db_table = "api_4_conversation"


class UserCanvas(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    user_id = CharField(max_length=255, null=False, help_text="user_id", index=True)
    title = CharField(max_length=255, null=True, help_text="Canvas title")

    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    release = BooleanField(null=False, help_text="is released", default=False, index=True)
    description = TextField(null=True, help_text="Canvas description")
    canvas_type = CharField(max_length=32, null=True, help_text="Canvas type", index=True)
    canvas_category = CharField(max_length=32, null=False, default="agent_canvas", help_text="Canvas category: agent_canvas|dataflow_canvas", index=True)
    dsl = JSONField(null=True, default={})

    class Meta:
        db_table = "user_canvas"


class CanvasTemplate(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    title = JSONField(null=True, default=dict, help_text="Canvas title")
    description = JSONField(null=True, default=dict, help_text="Canvas description")
    canvas_type = CharField(max_length=32, null=True, help_text="Canvas type", index=True)
    canvas_types = ListField(null=True, default=list, help_text="Canvas types")
    canvas_category = CharField(max_length=32, null=False, default="agent_canvas", help_text="Canvas category: agent_canvas|dataflow_canvas", index=True)
    dsl = JSONField(null=True, default={})

    class Meta:
        db_table = "canvas_template"


class UserCanvasVersion(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_canvas_id = CharField(max_length=255, null=False, help_text="user_canvas_id", index=True)

    title = CharField(max_length=255, null=True, help_text="Canvas title")
    description = TextField(null=True, help_text="Canvas description")
    release = BooleanField(null=False, help_text="is released", default=False, index=True)
    dsl = JSONField(null=True, default={})

    class Meta:
        db_table = "user_canvas_version"


class MCPServer(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=False, help_text="MCP Server name")
    tenant_id = CharField(max_length=32, null=False, index=True)
    url = CharField(max_length=2048, null=False, help_text="MCP Server URL")
    server_type = CharField(max_length=32, null=False, help_text="MCP Server type")
    description = TextField(null=True, help_text="MCP Server description")
    variables = JSONField(null=True, default=dict, help_text="MCP Server variables")
    headers = JSONField(null=True, default=dict, help_text="MCP Server additional request headers")

    class Meta:
        db_table = "mcp_server"


class Search(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="Search name", index=True)
    description = TextField(null=True, help_text="KB description")
    created_by = CharField(max_length=32, null=False, index=True)
    search_config = JSONField(
        null=False,
        default={
            "kb_ids": [],
            "doc_ids": [],
            "similarity_threshold": 0.2,
            "vector_similarity_weight": 0.3,
            "use_kg": False,
            # rerank settings
            "rerank_id": "",
            "top_k": 1024,
            # chat settings
            "summary": False,
            "chat_id": "",
            # Leave it here for reference, don't need to set default values
            "llm_setting": {
                # "temperature": 0.1,
                # "top_p": 0.3,
                # "frequency_penalty": 0.7,
                # "presence_penalty": 0.4,
            },
            "chat_settingcross_languages": [],
            "highlight": False,
            "keyword": False,
            "web_search": False,
            "related_search": False,
            "query_mindmap": False,
        },
    )
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "search"


class PipelineOperationLog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="Pipeline ID", index=True)
    pipeline_title = CharField(max_length=32, null=True, help_text="Pipeline title", index=True)
    parser_id = CharField(max_length=32, null=False, help_text="Parser ID", index=True)
    document_name = CharField(max_length=255, null=False, help_text="File name")
    document_suffix = CharField(max_length=255, null=False, help_text="File suffix")
    document_type = CharField(max_length=255, null=False, help_text="Document type")
    source_from = CharField(max_length=255, null=False, help_text="Source")
    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    process_begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)
    dsl = JSONField(null=True, default=dict)
    task_type = CharField(max_length=32, null=False, default="")
    operation_status = CharField(max_length=32, null=False, help_text="Operation status")
    avatar = TextField(null=True, help_text="avatar base64 string")
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "pipeline_operation_log"


class Connector(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="Search name", index=False)
    source = CharField(max_length=128, null=False, help_text="Data source", index=True)
    input_type = CharField(max_length=128, null=False, help_text="poll/event/..", index=True)
    config = JSONField(null=False, default={})
    refresh_freq = IntegerField(default=0, index=False)
    prune_freq = IntegerField(default=0, index=False)
    timeout_secs = IntegerField(default=3600, index=False)
    indexing_start = DateTimeField(null=True, index=True)
    status = CharField(max_length=16, null=True, help_text="schedule", default="schedule", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "connector"


class Connector2Kb(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    connector_id = CharField(max_length=32, null=False, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)
    auto_parse = CharField(max_length=1, null=False, default="1", index=False)

    class Meta:
        db_table = "connector2kb"


class DateTimeTzField(CharField):
    field_type = 'VARCHAR'

    def db_value(self, value: datetime|None) -> str|None:
        if value is not None:
            if value.tzinfo is not None:
                return value.isoformat()
            else:
                return value.replace(tzinfo=timezone.utc).isoformat()
        return value

    def python_value(self, value: str|None) -> datetime|None:
        if value is not None:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                import pytz
                return dt.replace(tzinfo=pytz.UTC)
            return dt
        return value


class SyncLogs(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    connector_id = CharField(max_length=32, index=True)
    status = CharField(max_length=128, null=False, help_text="Processing status", index=True)
    from_beginning = CharField(max_length=1, null=True, help_text="", default="0", index=False)
    new_docs_indexed = IntegerField(default=0, index=False)
    total_docs_indexed = IntegerField(default=0, index=False)
    docs_removed_from_index = IntegerField(default=0, index=False)
    error_msg = TextField(null=False, help_text="process message", default="")
    error_count = IntegerField(default=0, index=False)
    full_exception_trace = TextField(null=True, help_text="process message", default="")
    time_started = DateTimeField(null=True, index=True)
    poll_range_start = DateTimeTzField(max_length=255, null=True, index=True)
    poll_range_end = DateTimeTzField(max_length=255, null=True, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "sync_logs"


class EvaluationDataset(DataBaseModel):
    """Ground truth dataset for RAG evaluation"""
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True, help_text="tenant ID")
    name = CharField(max_length=255, null=False, index=True, help_text="dataset name")
    description = TextField(null=True, help_text="dataset description")
    kb_ids = JSONField(null=False, help_text="knowledge base IDs to evaluate against")
    created_by = CharField(max_length=32, null=False, index=True, help_text="creator user ID")
    create_time = BigIntegerField(null=False, index=True, help_text="creation timestamp")
    update_time = BigIntegerField(null=False, help_text="last update timestamp")
    status = IntegerField(null=False, default=1, help_text="1=valid, 0=invalid")

    class Meta:
        db_table = "evaluation_datasets"


class EvaluationCase(DataBaseModel):
    """Individual test case in an evaluation dataset"""
    id = CharField(max_length=32, primary_key=True)
    dataset_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_datasets")
    question = TextField(null=False, help_text="test question")
    reference_answer = TextField(null=True, help_text="optional ground truth answer")
    relevant_doc_ids = JSONField(null=True, help_text="expected relevant document IDs")
    relevant_chunk_ids = JSONField(null=True, help_text="expected relevant chunk IDs")
    metadata = JSONField(null=True, help_text="additional context/tags")
    create_time = BigIntegerField(null=False, help_text="creation timestamp")

    class Meta:
        db_table = "evaluation_cases"


class EvaluationRun(DataBaseModel):
    """A single evaluation run"""
    id = CharField(max_length=32, primary_key=True)
    dataset_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_datasets")
    dialog_id = CharField(max_length=32, null=False, index=True, help_text="dialog configuration being evaluated")
    name = CharField(max_length=255, null=False, help_text="run name")
    config_snapshot = JSONField(null=False, help_text="dialog config at time of evaluation")
    metrics_summary = JSONField(null=True, help_text="aggregated metrics")
    status = CharField(max_length=32, null=False, default="PENDING", help_text="PENDING/RUNNING/COMPLETED/FAILED")
    created_by = CharField(max_length=32, null=False, index=True, help_text="user who started the run")
    create_time = BigIntegerField(null=False, index=True, help_text="creation timestamp")
    complete_time = BigIntegerField(null=True, help_text="completion timestamp")

    class Meta:
        db_table = "evaluation_runs"


class EvaluationResult(DataBaseModel):
    """Result for a single test case in an evaluation run"""
    id = CharField(max_length=32, primary_key=True)
    run_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_runs")
    case_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_cases")
    generated_answer = TextField(null=False, help_text="generated answer")
    retrieved_chunks = JSONField(null=False, help_text="chunks that were retrieved")
    metrics = JSONField(null=False, help_text="all computed metrics")
    execution_time = FloatField(null=False, help_text="response time in seconds")
    token_usage = JSONField(null=True, help_text="prompt/completion tokens")
    create_time = BigIntegerField(null=False, help_text="creation timestamp")

    class Meta:
        db_table = "evaluation_results"


class Memory(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=128, null=False, index=False, help_text="Memory name")
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    memory_type = IntegerField(null=False, default=1, index=True, help_text="Bit flags (LSB->MSB): 1=raw, 2=semantic, 4=episodic, 8=procedural. E.g., 5 enables raw + episodic.")
    storage_type = CharField(max_length=32, default='table', null=False, index=True, help_text="table|graph")
    embd_id = CharField(max_length=128, null=False, index=False, help_text="embedding model ID")
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    llm_id = CharField(max_length=128, null=False, index=False, help_text="chat model ID")
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    permissions = CharField(max_length=16, null=False, index=True, help_text="me|team", default="me")
    description = TextField(null=True, help_text="description")
    memory_size = IntegerField(default=5242880, null=False, index=False)
    forgetting_policy = CharField(max_length=32, null=False, default="FIFO", index=False, help_text="LRU|FIFO")
    temperature = FloatField(default=0.5, index=False)
    system_prompt = TextField(null=True, help_text="system prompt", index=False)
    user_prompt = TextField(null=True, help_text="user prompt", index=False)

    class Meta:
        db_table = "memory"

class SystemSettings(DataBaseModel):
    name = CharField(max_length=128, primary_key=True)
    source = CharField(max_length=32, null=False, index=False)
    data_type = CharField(max_length=32, null=False, index=False)
    value = TextField(null=False, help_text="Configuration value (JSON, string, etc.)")
    class Meta:
        db_table = "system_settings"


class DocumentAnalysisTemplate(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=False, help_text="模板名称")
    doc_type = CharField(max_length=64, null=False, index=True, help_text="文档类型: bid/contract/law/general")
    description = TextField(null=True, help_text="模板说明")
    dimensions = JSONField(default=list, help_text="分析维度配置")
    prompt_templates = JSONField(default=dict, help_text="Prompt模板")
    chunk_merge_rule = JSONField(default=dict, help_text="章节合并规则")
    llm_id = CharField(max_length=64, null=True, help_text="Chat模型ID或名称")
    is_default = BooleanField(default=False, index=True, help_text="是否默认模板")
    is_system = BooleanField(default=False, index=True, help_text="是否系统模板")
    tenant_id = CharField(max_length=32, null=True, index=True, help_text="租户ID")

    class Meta:
        db_table = "document_analysis_template"


class DocumentAnalysisResult(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, null=False, index=True, help_text="文档ID")
    template_id = CharField(max_length=32, null=False, index=True, help_text="模板ID")
    status = CharField(max_length=16, null=False, default="pending", index=True, help_text="状态: pending/running/completed/failed")
    progress = IntegerField(default=0, help_text="进度 0-100")
    result = JSONField(default=list, help_text="分析结果")
    error_message = TextField(null=True, help_text="错误信息")
    doc_name = CharField(max_length=255, null=True, help_text="文档名称")
    kb_id = CharField(max_length=32, null=False, index=True, help_text="知识库ID")
    tenant_id = CharField(max_length=32, null=False, index=True, help_text="租户ID")
    llm_id = CharField(max_length=64, null=True, help_text="使用的模型ID")

    class Meta:
        db_table = "document_analysis_result"


class ScheduledTask(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=False, help_text="task display name")
    description = TextField(null=True, help_text="task description", default="")
    script_path = TextField(null=False, help_text="absolute path to Python script")
    script_args = TextField(null=True, help_text="CLI arguments passed to script", default="")
    schedule_type = CharField(
        max_length=16, null=False, default="interval",
        help_text="cron|interval"
    )
    cron_expression = CharField(max_length=64, null=True, help_text="cron expr", default="")
    interval_seconds = IntegerField(null=True, default=3600, help_text="seconds between runs")
    enabled = BooleanField(default=True, help_text="whether task is active")
    last_run_time = BigIntegerField(null=True, help_text="timestamp of last execution")
    last_run_status = CharField(max_length=16, null=True, help_text="success|fail|running", default="")
    next_run_time = BigIntegerField(null=True, help_text="computed next execution timestamp")
    timeout = IntegerField(default=3600, help_text="max execution seconds")
    max_retries = IntegerField(default=0, help_text="retry count on failure")
    retry_count = IntegerField(default=0)
    target_url = TextField(null=True, help_text="crawl target URL", default="")
    llm_id = CharField(max_length=64, null=True, help_text="LLM factory for image analysis", default="")
    llm_model_name = CharField(max_length=128, null=True, help_text="LLM model name for image analysis", default="")
    kb_id = CharField(max_length=32, null=True, index=True, help_text="target knowledge base ID", default="")
    access_token = TextField(null=True, help_text="access token for authenticated crawling", default="")

    class Meta:
        db_table = "scheduled_task"


class ScheduledTaskLog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    task_id = CharField(max_length=32, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    status = CharField(max_length=16, null=False, default="running", help_text="running|success|fail")
    start_time = BigIntegerField(null=True)
    end_time = BigIntegerField(null=True)
    duration = FloatField(null=True, help_text="execution duration in seconds")
    output = LongTextField(null=True, help_text="stdout captured from script", default="")
    error_msg = LongTextField(null=True, help_text="stderr or exception message", default="")
    pid = IntegerField(null=True, help_text="OS process ID")

    class Meta:
        db_table = "scheduled_task_log"
        indexes = (
            (("task_id", "start_time"), False),
        )


class CrawlerState(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    site_id = CharField(max_length=128, null=False, index=True, help_text="site identifier from crawler_sites.yaml")
    tenant_id = CharField(max_length=32, null=False, index=True)
    section = CharField(max_length=64, null=False, default="default", help_text="section label for multi-section sites")
    processed_ids = JSONField(null=False, default=[], help_text="list of crawled item IDs for dedup")
    last_page = IntegerField(null=False, default=0, help_text="last completed page number")
    last_offset = IntegerField(null=False, default=0, help_text="last offset for offset-based pagination")
    extra_state = JSONField(null=False, default={}, help_text="arbitrary extra state for complex crawlers")

    class Meta:
        db_table = "crawler_state"
        indexes = (
            (("site_id", "tenant_id", "section"), True),  # unique
        )


class CrawlerTask(DataBaseModel):
    """crawl4ai 独立爬虫任务定义 (crawl4ai-service 体系, 区别于旧 scheduled_task)"""
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=False, help_text="task display name")
    description = TextField(null=True, default="", help_text="task description")
    site_id = CharField(max_length=128, null=False, index=True, help_text="site identifier, e.g. ccgp_zygg")
    target_url = TextField(null=False, help_text="listing page URL of the target section/tab")
    page_url_template = TextField(null=True, default="", help_text="pagination URL template containing {page}, empty = single page")
    start_page = IntegerField(default=1, help_text="first page number")
    max_pages = IntegerField(default=1, help_text="max pages to crawl per run")
    extraction_schema = JSONField(null=False, default={}, help_text="JsonCssExtractionStrategy schema for listing page: {baseSelector, fields}")
    detail_config = JSONField(null=False, default={}, help_text="detail page config: {enabled, url_field, base_url, content_selector, attachment_extensions}")
    headers = JSONField(null=False, default={}, help_text="custom request headers")
    output_targets = JSONField(null=False, default=["db"], help_text='["db","kb"]')
    kb_id = CharField(max_length=32, null=True, index=True, default="", help_text="target knowledge base ID")
    parser_id = CharField(max_length=32, null=True, default="naive", help_text="KB parser for uploaded docs")
    enabled = BooleanField(default=True)
    last_run_time = BigIntegerField(null=True, help_text="timestamp of last execution")
    last_run_status = CharField(max_length=16, null=True, default="", help_text="running|success|fail")
    last_run_summary = JSONField(null=False, default={}, help_text="last run stats: pages/items_found/items_new/errors")

    class Meta:
        db_table = "crawler_task"


class CrawlerResult(DataBaseModel):
    """crawl4ai 采集结果 (正文 markdown + 结构化 JSON + 附件 + KB 关联)"""
    id = CharField(max_length=32, primary_key=True, help_text="md5(site_id|source_url) for dedup")
    task_id = CharField(max_length=32, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    site_id = CharField(max_length=128, null=False, index=True)
    site_display = CharField(max_length=256, null=True, default="", index=True,
                             help_text="展示用站点串: '{中文名称} {域名}'，由 YAML name+site_url 在采集时拼接")
    category = CharField(max_length=32, null=False, default="bid", index=True,
                         help_text="bid|policy|personnel|news|other|objection")
    title = CharField(max_length=1024, null=False, default="")
    source_url = TextField(null=False)
    publish_date = CharField(max_length=64, null=True, index=True, default="", help_text="publish date string from listing")
    markdown = LongTextField(null=True, default="", help_text="detail page content as markdown")
    extracted_json = JSONField(null=False, default={}, help_text="structured fields extracted from listing/detail")
    attachments = JSONField(null=False, default=[], help_text="[{file_name, file_url, kb_doc_id, status}]")
    status = CharField(max_length=16, null=False, index=True, default="raw", help_text="raw|kb_uploaded|failed")
    kb_doc_id = CharField(max_length=32, null=True, default="", help_text="RAGFlow KB document ID of uploaded markdown")
    error_msg = TextField(null=True, default="")
    crawled_at = BigIntegerField(null=True, index=True, help_text="crawl timestamp (ms)")

    class Meta:
        db_table = "crawler_result"


class CollaborationDocument(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=False, index=True, help_text="document name")
    file_type = CharField(max_length=16, null=False, default="docx", index=True, help_text="docx|pdf")
    file_path = CharField(max_length=512, null=True, help_text="storage key in STORAGE_IMPL for generated file")
    content = JSONField(null=True, default={}, help_text="Lexical editor JSON state for rich editing")
    markdown_content = TextField(null=True, help_text="original markdown source from chat message")
    tenant_id = CharField(max_length=32, null=False, index=True)
    created_by = CharField(max_length=32, null=False, index=True)
    agent_id = CharField(max_length=32, null=True, index=True)
    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    folder_id = CharField(max_length=32, null=True, index=True, help_text="parent folder id")
    sort_order = IntegerField(default=0, help_text="sort order within folder")
    ydoc = BlobField(null=True, help_text="Yjs binary state for real-time collaboration sync")
    version = IntegerField(default=0, help_text="monotonic version counter for snapshot history")

    class Meta:
        db_table = "collaboration_document"


class CollaborationFolder(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=False, help_text="folder name")
    parent_id = CharField(max_length=32, null=True, index=True, help_text="parent folder id, null for root")
    tenant_id = CharField(max_length=32, null=False, index=True)
    created_by = CharField(max_length=32, null=False, index=True)
    sort_order = IntegerField(default=0, help_text="sort order among siblings")
    create_time = BigIntegerField(null=False, default=0)

    class Meta:
        db_table = "collaboration_folder"


class CollaborationDocumentACL(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, null=False, index=True, help_text="document id")
    user_id = CharField(max_length=32, null=False, index=True, help_text="collaborator user/tenant id")
    role = CharField(max_length=16, null=False, default="viewer", help_text="owner|editor|viewer|commenter", index=True)
    granted_by = CharField(max_length=32, null=False, help_text="who granted this access")
    create_time = BigIntegerField(null=False, default=0)

    class Meta:
        db_table = "collaboration_document_acl"
        indexes = (
            (("document_id", "user_id"), True),  # UNIQUE
        )


class CollaborationComment(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=32, null=False, index=True)
    parent_comment_id = CharField(max_length=32, null=True, index=True)
    anchor_block_key = CharField(max_length=64, null=True)
    anchor_offset_start = IntegerField(null=True)
    anchor_offset_end = IntegerField(null=True)
    content = TextField(null=False)
    resolved = BooleanField(default=False, index=True)
    deleted_at = BigIntegerField(null=True)
    create_time = BigIntegerField(null=False, default=0)
    update_time = BigIntegerField(null=False, default=0)

    class Meta:
        db_table = "collaboration_comment"


class CollaborationShareLink(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, null=False, index=True, unique=True)
    token = CharField(max_length=32, null=False, unique=True, index=True)
    permission = CharField(max_length=16, null=False, default="view")
    password_hash = CharField(max_length=256, null=True)
    expires_at = BigIntegerField(null=True)
    created_by = CharField(max_length=32, null=False)
    create_time = BigIntegerField(null=False, default=0)
    update_time = BigIntegerField(null=False, default=0)

    class Meta:
        db_table = "collaboration_share_link"


class CollaborationAttachment(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, null=False, index=True)
    file_name = CharField(max_length=256, null=False)
    file_size = BigIntegerField(null=False, default=0)
    mime_type = CharField(max_length=128, null=False)
    storage_key = CharField(max_length=512, null=False)
    uploader_id = CharField(max_length=32, null=False, index=True)
    create_time = BigIntegerField(null=False, default=0)

    class Meta:
        db_table = "collaboration_attachment"


class CollaborationAuditLog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, index=True)
    document_id = CharField(max_length=32, null=True, index=True)
    action = CharField(max_length=32, null=False, index=True)
    detail = JSONField(null=True)
    ip_address = CharField(max_length=64, null=True)
    create_time = BigIntegerField(null=False, default=0)

    class Meta:
        db_table = "collaboration_audit_log"


class CollaborationDocumentVersion(DataBaseModel):
    """历史版本快照表。每次 save_ydoc_state 写入一条，保留最新 20 条。"""
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, null=False, index=True)
    version = IntegerField(null=False, index=True, help_text="对应 CollaborationDocument.version 快照时刻的值")
    ydoc_snapshot = BlobField(null=True, help_text="Yjs binary state snapshot")
    content_snapshot = JSONField(null=True, help_text="Univer IDocumentData JSON snapshot")
    created_by = CharField(max_length=32, null=False, index=True)
    create_time = BigIntegerField(null=False, default=0)

    class Meta:
        db_table = "collaboration_document_version"


class CollaborationFormatRule(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=128, null=False, help_text="rule name")
    description = TextField(null=True, help_text="rule description")
    config = JSONField(null=False, default={}, help_text="font_name, font_size, line_spacing, margins, alignment, etc.")
    tenant_id = CharField(max_length=32, null=False, index=True)
    created_by = CharField(max_length=32, null=False, index=True)
    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)

    class Meta:
        db_table = "collaboration_format_rule"


class BidProject(DataBaseModel):
    id = BigIntegerField(primary_key=True, help_text="标讯信息ID")
    title = TextField(null=True, help_text="标题(去HTML标签)")
    title_html = TextField(null=True, help_text="标题(含高亮标签)")
    content = TextField(null=True, help_text="命中内容摘要")
    publish_time = DateTimeField(null=True, index=True, help_text="发布时间")
    news_type_id = IntegerField(null=True, help_text="信息类别(老版):1招标2中标3合同")
    project_class_id = CharField(max_length=20, null=True, index=True, help_text="新分类ID")
    purchase_type_id = CharField(max_length=20, null=True, help_text="采购类别ID")
    project_money = CharField(max_length=50, null=True, help_text="项目金额(带单位)")
    provice_code = CharField(max_length=20, null=True, help_text="省代码")
    city_code = CharField(max_length=20, null=True, help_text="市代码")
    county_code = CharField(max_length=20, null=True, help_text="区/县代码")
    industry_codes = JSONField(null=True, help_text="行业code数组")
    part_a_names = JSONField(null=True, help_text="甲方名称数组")
    part_b_names = JSONField(null=True, help_text="乙方名称数组")
    has_file = IntegerField(null=True, help_text="是否有附件:0=无,1=有")
    contract_end_date = CharField(max_length=20, null=True, help_text="合同到期时间")
    se_keywords = CharField(max_length=200, null=True, help_text="API返回的搜索关键词")
    score = FloatField(null=True, help_text="相关度得分")
    source_type = CharField(max_length=10, null=True, help_text="数据来源")
    raw_json = JSONField(null=True, help_text="原始返回JSON(备用)")
    sync_batch_id = CharField(max_length=36, null=True, help_text="同步批次ID")
    created_at = DateTimeField(null=True, index=True)
    updated_at = DateTimeField(null=True, index=True)
    fetched_at = DateTimeField(null=True, help_text="搜索缓存获取时间")
    cache_expires_at = DateTimeField(null=True, help_text="缓存过期时间")

    class Meta:
        db_table = "bid_project"


class BidProjectDetail(DataBaseModel):
    id = BigIntegerField(primary_key=True)
    project_id = BigIntegerField(null=False, unique=True, help_text="关联bid_project.id")
    content_html = TextField(null=True, help_text="完整HTML内容")
    news_type_id = IntegerField(null=True, help_text="信息类别ID:1招标2中标3合同")
    project_class_name = CharField(max_length=100, null=True, help_text="项目子分类名称")
    purchase_type_id = CharField(max_length=20, null=True, help_text="采购类别ID")
    industry_name = TextField(null=True, help_text="行业分类名称")
    part_a_name = TextField(null=True, help_text="甲方名称")
    part_b_name = TextField(null=True, help_text="乙方名称")
    agent_name = TextField(null=True, help_text="代理机构名称")
    project_money = CharField(max_length=50, null=True, help_text="项目金额")
    provice_code = CharField(max_length=20, null=True, help_text="省代码")
    city_code = CharField(max_length=20, null=True, help_text="市代码")
    county_code = CharField(max_length=20, null=True, help_text="区/县代码")
    fetched_at = DateTimeField(null=True, help_text="获取时间")
    cache_expires_at = DateTimeField(null=True, help_text="缓存过期时间")
    created_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_project_detail"


class BidProjectStructure(DataBaseModel):
    id = BigIntegerField(primary_key=True)
    project_id = BigIntegerField(null=False, unique=True, help_text="关联bid_project.id")
    project_name = TextField(null=True, help_text="项目名称")
    project_numbers = JSONField(null=True, help_text="项目编号数组")
    section_codes = JSONField(null=True, help_text="标段编号数组")
    budget_money = JSONField(null=True, help_text="预算金额数组")
    bid_money = JSONField(null=True, help_text="中标金额数组")
    bid_start_date = DateTimeField(null=True, help_text="开标日期")
    bid_start_address = JSONField(null=True, help_text="开标地点")
    sign_up_stop_date = DateTimeField(null=True, help_text="报名截止日期")
    party_a_info = JSONField(null=True, help_text="甲方信息")
    party_b_info = JSONField(null=True, help_text="乙方信息")
    agency_info = JSONField(null=True, help_text="代理机构信息")
    bid_companies = JSONField(null=True, help_text="投标企业")
    sbkj_bid_url = CharField(max_length=500, null=True, help_text="世舶科技静态页")
    collect_url = CharField(max_length=500, null=True, help_text="采集源网址")
    fetched_at = DateTimeField(null=True)
    cache_expires_at = DateTimeField(null=True)
    created_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_project_structure"


class BidProjectFile(DataBaseModel):
    project_file_id = BigIntegerField(primary_key=True)
    project_id = BigIntegerField(null=False, index=True, help_text="关联bid_project.id")
    file_name = CharField(max_length=500, null=True, help_text="附件名称")
    file_url = CharField(max_length=1000, null=True, help_text="下载地址")
    file_suffix = CharField(max_length=20, null=True, help_text="文件后缀")
    file_size = FloatField(null=True, help_text="文件大小(KB)")
    state = CharField(max_length=5, null=True, help_text="状态")
    local_path = CharField(max_length=500, null=True, help_text="本地存储路径")
    kb_document_id = CharField(max_length=64, null=True, help_text="关联的KB文档ID")
    publish_time = DateTimeField(null=True, help_text="项目发布时间")
    create_time = DateTimeField(null=True, help_text="附件创建时间")
    fetched_at = DateTimeField(null=True)
    downloaded_at = DateTimeField(null=True)
    created_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_project_file"


class BidProjectParse(DataBaseModel):
    project_id = BigIntegerField(primary_key=True, help_text="关联bid_project.id")
    kb_id = CharField(max_length=64, null=False, help_text="知识库ID")
    status = CharField(max_length=20, default="pending", help_text="pending/parsing/done/fail")
    progress = FloatField(default=0, help_text="解析进度 0-1")
    progress_msg = TextField(null=True, help_text="进度消息")
    combined_doc_id = CharField(max_length=64, null=True, help_text="拼接文档的KB doc ID")
    queued_doc_ids = TextField(null=True, help_text="JSON list of all KB doc IDs queued for parsing")
    created_at = DateTimeField(null=True)
    updated_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_project_parse"


class BidConstructionProject(DataBaseModel):
    id = BigIntegerField(primary_key=True, help_text="拟在建项目ID(API返回)")
    title = TextField(null=True, help_text="项目标题")
    summary = TextField(null=True, help_text="项目摘要")
    publish_time = DateTimeField(null=True, index=True, help_text="发布时间")
    provice_code = CharField(max_length=20, null=True, help_text="省代码")
    city_code = CharField(max_length=20, null=True, help_text="市代码")
    county_code = CharField(max_length=20, null=True, help_text="区/县代码")
    has_file = IntegerField(null=True, help_text="是否有附件")
    score = IntegerField(null=True, help_text="匹配分数")
    raw_json = JSONField(null=True, help_text="搜索API原始JSON")
    detail_json = JSONField(null=True, help_text="详情API原始JSON")
    se_keywords = CharField(max_length=200, null=True, help_text="搜索关键词")
    fetched_at = DateTimeField(null=True, help_text="缓存获取时间")
    cache_expires_at = DateTimeField(null=True, help_text="缓存过期时间")
    created_at = DateTimeField(null=True)
    updated_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_construction_project"


class BidConstructionParse(DataBaseModel):
    project_id = BigIntegerField(primary_key=True, help_text="关联 bid_construction_project.id")
    kb_id = CharField(max_length=64, null=False, help_text="知识库ID")
    status = CharField(max_length=20, default="pending", help_text="pending/parsing/done/fail")
    progress = FloatField(default=0, help_text="解析进度 0-1")
    progress_msg = TextField(null=True, help_text="进度消息")
    combined_doc_id = CharField(max_length=64, null=True, help_text="拼接文档的KB doc ID")
    queued_doc_ids = TextField(null=True, help_text="JSON list of all KB doc IDs queued for parsing")
    created_at = DateTimeField(null=True)
    updated_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_construction_parse"


class BidContractParse(DataBaseModel):
    project_id = BigIntegerField(primary_key=True, help_text="关联 bid_project.id (news_type_id=3)")
    kb_id = CharField(max_length=64, null=False, help_text="知识库ID")
    status = CharField(max_length=20, default="pending", help_text="pending/parsing/done/fail")
    progress = FloatField(default=0, help_text="解析进度 0-1")
    progress_msg = TextField(null=True, help_text="进度消息")
    combined_doc_id = CharField(max_length=64, null=True, help_text="拼接文档的KB doc ID")
    queued_doc_ids = TextField(null=True, help_text="JSON list of all KB doc IDs queued for parsing")
    created_at = DateTimeField(null=True)
    updated_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_contract_parse"


class BidEnterpriseParse(DataBaseModel):
    company_name = CharField(max_length=256, primary_key=True, help_text="企业名称(唯一标识)")
    kb_id = CharField(max_length=64, null=False, help_text="知识库ID")
    status = CharField(max_length=20, default="pending", help_text="pending/parsing/done/fail")
    progress = FloatField(default=0, help_text="解析进度 0-1")
    progress_msg = TextField(null=True, help_text="进度消息")
    combined_doc_id = CharField(max_length=64, null=True, help_text="拼接文档的KB doc ID")
    queued_doc_ids = TextField(null=True, help_text="JSON list of all KB doc IDs queued for parsing")
    created_at = DateTimeField(null=True)
    updated_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_enterprise_parse"


class BidEnterpriseCache(DataBaseModel):
    company_name = CharField(max_length=200, null=False, index=True, help_text="企业名称")
    cache_type = CharField(max_length=20, null=False, index=True, help_text="缓存类型: profile/contacts/customers/suppliers")
    page_no = IntegerField(default=1, help_text="页码")
    page_size = IntegerField(default=20, help_text="每页条数")
    response_json = JSONField(null=True, help_text="API返回的data字段JSON")
    fetched_at = DateTimeField(null=True, help_text="获取时间")
    cache_expires_at = DateTimeField(null=True, help_text="缓存过期时间")
    created_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_enterprise_cache"
        indexes = (
            (("company_name", "cache_type", "page_no", "page_size"), True),
        )


class BidEnterpriseBusiness(DataBaseModel):
    keyword = CharField(max_length=256, primary_key=True, help_text="查询关键词(公司名或统一社会信用代码)")
    response_json = JSONField(null=True, help_text="企业工商信息API完整响应data字段")
    fetched_at = DateTimeField(null=True, help_text="获取时间")
    cache_expires_at = DateTimeField(null=True, help_text="缓存过期时间")
    created_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_enterprise_business"


class BidTenderSearch(DataBaseModel):
    id = CharField(max_length=64, primary_key=True, help_text="sha256(projectNumber|title)")
    keyword_hash = CharField(max_length=64, null=False, index=True, help_text="sha256(keyword)")
    keyword = CharField(max_length=500, null=False, help_text="搜索关键词")

    title = TextField(null=True, help_text="公告标题")
    project_name = TextField(null=True, help_text="项目名称")
    project_number = CharField(max_length=200, null=True, help_text="项目编号")
    publish_time = DateTimeField(null=True, index=True, help_text="公告发布时间")
    announcement_type = CharField(max_length=50, null=True, help_text="公告类型")
    announcement_type_code = IntegerField(null=True, help_text="公告类型编码")
    bidding_stage = CharField(max_length=50, null=True, help_text="招投标阶段")
    bidding_stage_code = IntegerField(null=True, help_text="招投标阶段编码")
    procurement_method = CharField(max_length=50, null=True, help_text="采购方式")
    procurement_method_code = IntegerField(null=True, help_text="采购方式编码")
    industry_type = CharField(max_length=100, null=True, help_text="行业分类")
    target_item_type = CharField(max_length=100, null=True, help_text="标的物类型")
    project_region_province = CharField(max_length=50, null=True, help_text="项目区域-省份")
    project_region_province_code = CharField(max_length=20, null=True, help_text="省份行政区划代码")
    project_region_city = CharField(max_length=50, null=True, help_text="项目区域-城市")
    project_region_city_code = CharField(max_length=20, null=True, help_text="城市行政区划代码")
    content_url = CharField(max_length=1000, null=True, help_text="招投标公告原文链接")

    project_budget_amount = CharField(max_length=50, null=True, help_text="项目预算-金额")
    project_budget_amount_unit = CharField(max_length=10, null=True, help_text="项目预算-金额单位")
    total_amount = CharField(max_length=50, null=True, help_text="中标总金额")
    total_amount_unit = CharField(max_length=10, null=True, help_text="中标总金额单位")

    bid_document_start_time = CharField(max_length=30, null=True, help_text="标书获取开始时间")
    bid_document_end_time = CharField(max_length=30, null=True, help_text="标书获取截止时间")
    bidding_start_time = CharField(max_length=30, null=True, help_text="投标开始时间")
    bidding_end_time = CharField(max_length=30, null=True, help_text="投标结束时间")
    opening_bid_time = CharField(max_length=30, null=True, help_text="开标时间")
    contract_num = CharField(max_length=200, null=True, help_text="合同编号")

    purchase_agency = JSONField(null=True, help_text="采购单位&代理机构")
    win_candidate = JSONField(null=True, help_text="中标企业&候选单位")
    contacts_purchase_agency = JSONField(null=True, help_text="联系方式-采购单位&代理机构")
    contacts_win_candidate = JSONField(null=True, help_text="联系方式-中标企业&候选单位")

    search_mode = IntegerField(null=True, help_text="搜索模式: 1-精准 2-模糊")
    announcement_type_filter = CharField(max_length=50, null=True, help_text="搜索时传入的公告类型")
    province_code_filter = CharField(max_length=20, null=True, help_text="搜索时传入的省份代码")
    city_code_filter = CharField(max_length=20, null=True, help_text="搜索时传入的城市代码")

    raw_json = JSONField(null=True, help_text="API原始返回JSON")
    fetched_at = DateTimeField(null=True, help_text="缓存获取时间")
    cache_expires_at = DateTimeField(null=True, help_text="缓存过期时间")
    created_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_tender_search"
        indexes = (
            (("keyword_hash", "cache_expires_at"), False),
        )


class BidSyncLog(DataBaseModel):
    id = BigIntegerField(primary_key=True)
    batch_id = CharField(max_length=36, unique=True, help_text="同步批次ID")
    api_name = CharField(max_length=100, help_text="调用的API名称")
    sync_type = CharField(max_length=20, help_text="full/incremental")
    date_range_start = DateTimeField(null=True)
    date_range_end = DateTimeField(null=True)
    total_fetched = IntegerField(default=0)
    total_new = IntegerField(default=0)
    total_updated = IntegerField(default=0)
    status = CharField(max_length=20, default="running", help_text="running/success/failed")
    error_msg = TextField(null=True)
    started_at = DateTimeField(null=True)
    completed_at = DateTimeField(null=True)
    created_at = DateTimeField(null=True)

    class Meta:
        db_table = "bid_sync_log"


class UserLoginLog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, index=True)
    email = CharField(max_length=255, null=False, index=True)
    nickname = CharField(max_length=100, null=True)
    login_time = DateTimeField(null=False, index=True)
    ip = CharField(max_length=45, null=True)
    device_type = CharField(max_length=32, default="web")
    device_name = CharField(max_length=255, null=True)
    login_channel = CharField(max_length=32, null=True)
    user_agent = TextField(null=True)
    status = CharField(max_length=1, default="1")

    class Meta:
        db_table = "user_login_log"


class AreaCode(DataBaseModel):
    code = CharField(max_length=12, primary_key=True, help_text="行政区划编码")
    name = CharField(max_length=50, null=False, help_text="区划名称")
    parent_code = CharField(max_length=12, default="0", help_text="上级编码, 0=顶级")
    level = IntegerField(default=0, help_text="层级: 1=省, 2=市, 3=区县")

    class Meta:
        db_table = "area_code"


class WechatMpAccount(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    mp_name = CharField(max_length=255, null=False, help_text="公众号名称")
    faker_id = CharField(max_length=255, null=False, help_text="公众号fake ID")
    mp_cover = CharField(max_length=500, null=True, help_text="公众号封面图URL")
    mp_intro = TextField(null=True, help_text="公众号简介")
    status = IntegerField(default=1, help_text="状态: 1=正常, 0=禁用")
    created_at = DateTimeField(null=True)
    updated_at = DateTimeField(null=True)

    class Meta:
        db_table = "wechat_mp_account"


class WechatMpAuth(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, unique=True, index=True)
    cookie = TextField(null=True, help_text="微信登录cookie字符串")
    token = CharField(max_length=500, null=True, help_text="微信登录token")
    expiry = DateTimeField(null=True, help_text="token/cookie过期时间")
    ext_data = TextField(null=True, help_text="扩展数据(公众号名称、头像等JSON)")
    updated_at = DateTimeField(null=True)

    class Meta:
        db_table = "wechat_mp_auth"


class Favorite(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=32, null=False, index=True)
    title = CharField(max_length=255, null=False, help_text="收藏标题")
    message_ids = JSONField(null=False, help_text="消息ID列表(JSON array)")
    messages_data = JSONField(null=True, help_text="完整消息数据(role+content+reference)")
    agent_id = CharField(max_length=32, null=True, help_text="关联的agent ID")
    conversation_id = CharField(max_length=32, null=True, help_text="关联的conversation ID")
    created_at = DateTimeField(null=True)
    updated_at = DateTimeField(null=True)

    class Meta:
        db_table = "favorite"


class StarredSite(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=32, null=False, index=True)
    site_name = CharField(max_length=255, null=False, help_text="网站名称")
    site_url = CharField(max_length=1024, null=False, help_text="网站URL")
    created_at = DateTimeField(null=True)
    updated_at = DateTimeField(null=True)

    class Meta:
        db_table = "starred_site"


# ---------------------------------------------------------------------------
# 智能采集（新系统）扩展表
#
# 与 CrawlerResult 一对一关联，按 category 路由写入：
#   - category=bid        → 不写扩展表，字段放 CrawlerResult.extracted_json
#   - category=policy     → CollectionPolicyExt
#   - category=personnel  → CollectionPersonnelExt
#   - category=news/other → 不写扩展表
# 与旧 bid_* 表族完全解耦，由 CollectionWriter 写入。
# ---------------------------------------------------------------------------


class CollectionPolicyExt(DataBaseModel):
    """政策法规类采集扩展字段（category=policy）。"""
    result_id = CharField(max_length=32, primary_key=True, help_text="FK -> crawler_result.id")
    doc_number = CharField(max_length=200, null=True, default="", help_text="发文字号，如 国发〔2024〕12号", index=True)
    issuing_authority = CharField(max_length=200, null=True, default="", help_text="发文机构")
    authority_level = CharField(max_length=50, null=True, default="", help_text="效力级别: 法律|行政法规|部门规章|地方性法规|规范性文件", index=True)
    topic_category = CharField(max_length=100, null=True, default="", help_text="主题分类，如 招投标|建筑工程|安全生产")
    effective_date = DateField(null=True, help_text="实施日期")
    expiry_date = DateField(null=True, help_text="失效日期")
    status = CharField(max_length=20, null=True, default="有效", help_text="有效|废止|修订中")
    legal_basis = CharField(max_length=500, null=True, default="", help_text="依据上位法")

    class Meta:
        db_table = "collection_policy_ext"


class CollectionPersonnelExt(DataBaseModel):
    """人员信息类采集扩展字段（category=personnel）。"""
    result_id = CharField(max_length=32, primary_key=True, help_text="FK -> crawler_result.id")
    person_name = CharField(max_length=100, null=True, default="", index=True, help_text="姓名")
    id_card_masked = CharField(max_length=50, null=True, default="", help_text="身份证号（脱敏）")
    cert_no = CharField(max_length=100, null=True, default="", index=True, help_text="证书编号")
    cert_type = CharField(max_length=100, null=True, default="", help_text="证书类型：一级建造师|监理工程师|造价工程师等")
    employer = CharField(max_length=200, null=True, default="", index=True, help_text="所属单位")
    specialty = CharField(max_length=200, null=True, default="", help_text="注册专业")
    position = CharField(max_length=100, null=True, default="", help_text="职务")
    valid_until = DateField(null=True, help_text="证书有效期")
    status = CharField(max_length=20, null=True, default="注册", help_text="注册|注销|转注")

    class Meta:
        db_table = "collection_personnel_ext"


class CollectionObjectionExt(DataBaseModel):
    """异议结果类采集扩展字段（category=objection）。"""
    result_id = CharField(max_length=32, primary_key=True, help_text="FK -> crawler_result.id")
    record_no = CharField(max_length=100, null=True, default="", index=True, help_text="编号，如 A02202607001306")
    publication_time = CharField(max_length=64, null=True, default="", help_text="公示时间")
    tender_no = CharField(max_length=100, null=True, default="", index=True, help_text="招标编号")
    owner_unit = CharField(max_length=200, null=True, default="", index=True, help_text="业主单位")
    tender_agency = CharField(max_length=200, null=True, default="", help_text="招标代理机构")
    related_sections = CharField(max_length=500, null=True, default="", help_text="相关标段(包)")
    objector_name = CharField(max_length=200, null=True, default="", index=True, help_text="异议人名称")
    objected_party_name = CharField(max_length=200, null=True, default="", index=True, help_text="被异议人名称")
    objection_time = CharField(max_length=64, null=True, default="", index=True, help_text="异议时间")
    objection_type = CharField(max_length=100, null=True, default="", index=True, help_text="异议类型")
    objection_content = TextField(null=True, default="", help_text="异议内容")
    basis_and_reasons = TextField(null=True, default="", help_text="依据和理由")
    acceptance_time = CharField(max_length=64, null=True, default="", help_text="受理时间")
    processing_time = CharField(max_length=64, null=True, default="", help_text="处理时间")
    handling_opinion = TextField(null=True, default="", help_text="异议处理意见")
    processing_result = TextField(null=True, default="", help_text="处理结果")
    processing_basis = TextField(null=True, default="", help_text="处理依据")

    class Meta:
        db_table = "collection_objection_ext"


class CollectionZdgksxmlExt(DataBaseModel):
    """重点公开事项类采集扩展字段（category=zdgksxml，福建省交通运输厅公开事项目录）。"""
    result_id = CharField(max_length=32, primary_key=True, help_text="FK -> crawler_result.id")
    seq_no = IntegerField(null=True, help_text="序号")
    category_l1 = CharField(max_length=64, null=True, default="", index=True, help_text="公开类别（一级）")
    category_l2 = CharField(max_length=64, null=True, default="", help_text="二级公开类别")
    matter = CharField(max_length=128, null=True, default="", index=True, help_text="公开事项")
    disclosure_content = TextField(null=True, default="", help_text="公开内容")
    legal_doc_title = CharField(max_length=255, null=True, default="", help_text="公开依据文件名")
    legal_doc_url = CharField(max_length=512, null=True, default="", index=True, help_text="依据文件超链接")
    legal_doc_clause = TextField(null=True, default="", help_text="公开依据文件条款（多条以空行分隔）")
    disclosure_deadline = CharField(max_length=255, null=True, default="", help_text="公开时限")
    disclosure_period = CharField(max_length=64, null=True, default="", help_text="公开期限")
    disclosure_subject = CharField(max_length=128, null=True, default="", help_text="公开主体")
    disclosure_duty = CharField(max_length=255, null=True, default="", help_text="公开责任")
    disclosure_method = CharField(max_length=128, null=True, default="", help_text="公开方式")
    disclosure_channel = CharField(max_length=255, null=True, default="", help_text="公开渠道")

    class Meta:
        db_table = "collection_zdgksxml_ext"


# ── 智能采集通知系统 ──────────────────────────────────────────────
class Notification(DataBaseModel):
    """采集通知主体：一个 site 一轮新增聚合 = 1 条记录。"""
    id = CharField(max_length=64, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, default="system", index=True,
                          help_text="沿用 collection_app _SHARED_TENANT='system' 模型，全局共享")
    site_id = CharField(max_length=128, null=False, index=True)
    site_display = CharField(max_length=256, null=True, default="")
    category = CharField(max_length=32, null=False, default="news", index=True)
    batch_key = CharField(max_length=160, null=False, unique=True,
                          help_text="{site_id}::{minute_ts} 幂等键")
    title = CharField(max_length=256, null=False, default="")
    summary = TextField(null=True, default="")
    result_ids = JSONField(null=False, default=list)
    result_count = IntegerField(null=False, default=0)
    publish_range = CharField(max_length=64, null=True, default="")
    created_at = BigIntegerField(null=False, default=0, index=True)

    class Meta:
        db_table = "notification"


class NotificationUser(DataBaseModel):
    """用户维度未读记录（已阅状态）。"""
    id = CharField(max_length=64, primary_key=True)
    notification_id = CharField(max_length=64, null=False, index=True)
    user_id = CharField(max_length=64, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, default="system", index=True)
    is_read = BooleanField(null=False, default=False, index=True)
    read_at = BigIntegerField(null=True, default=None)

    class Meta:
        db_table = "notification_user"
        indexes = (
            (("user_id", "notification_id"), True),  # 复合唯一
            (("user_id", "is_read"), False),  # 未读列表查询索引
        )


class NotificationSubscription(DataBaseModel):
    """用户订阅偏好（site_ids/categories 为空 = 全订阅）。"""
    id = CharField(max_length=64, primary_key=True)
    user_id = CharField(max_length=64, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, default="system", index=True)
    site_ids = JSONField(null=False, default=list, help_text="[] = 全订阅")
    categories = JSONField(null=False, default=list, help_text="[] = 全订阅")
    browser_push = BooleanField(null=False, default=True)
    force_modal = BooleanField(null=False, default=True)

    class Meta:
        db_table = "notification_subscription"
        indexes = (
            (("user_id", "tenant_id"), True),
        )


# ── C端流程（文件流转工作流） ──────────────────────────────────────
class FlowInstance(DataBaseModel):
    """流程实例：一份文件在 发起人→领导→处理人→发起人汇总 之间流转。"""
    id = CharField(max_length=32, primary_key=True)
    title = CharField(max_length=256, null=False, default="", help_text="流程标题")
    initiator_id = CharField(max_length=32, null=False, index=True, help_text="发起人 user_id（角色1，兼汇总人）")
    leader_id = CharField(max_length=32, null=False, index=True, help_text="领导 user_id（审批人）")
    handler_id = CharField(max_length=32, null=False, index=True, help_text="处理人 user_id（角色2）")
    status = CharField(max_length=32, null=False, default="initiator", index=True,
                       help_text="initiator|leader|handler|summary|archived|cancelled（当前文件在谁手上）")
    current_version_id = CharField(max_length=32, null=False, default="", help_text="当前最新版本 id")

    class Meta:
        db_table = "flow_instance"


class FlowVersion(DataBaseModel):
    """文件版本：核心表。每次人工上传 / AI 产出生成一个新版本，全历史保留。"""
    id = CharField(max_length=32, primary_key=True)
    flow_id = CharField(max_length=32, null=False, index=True, help_text="FK -> flow_instance.id")
    version_no = IntegerField(null=False, default=1, help_text="版本号，从 1 递增")
    file_name = CharField(max_length=512, null=False, default="", help_text="展示文件名")
    file_path = CharField(max_length=1024, null=False, default="", help_text="MinIO object name")
    file_type = CharField(max_length=64, null=False, default="", help_text="MIME 或扩展名")
    file_size = BigIntegerField(null=False, default=0, help_text="字节数")
    source = CharField(max_length=32, null=False, default="manual_upload",
                       help_text="manual_upload|ai_output")
    created_by = CharField(max_length=32, null=False, default="", help_text="上传人 user_id")
    node_status = CharField(max_length=32, null=False, default="initiator",
                            help_text="产生该版本时的流程状态")

    class Meta:
        db_table = "flow_version"
        indexes = ((("flow_id", "version_no"), True),)


class FlowComment(DataBaseModel):
    """批注意见：针对某个文件版本的文字意见。"""
    id = CharField(max_length=32, primary_key=True)
    flow_id = CharField(max_length=32, null=False, index=True)
    version_id = CharField(max_length=32, null=False, index=True, help_text="意见针对的版本")
    user_id = CharField(max_length=32, null=False, index=True, help_text="意见人")
    content = TextField(null=False, default="", help_text="意见内容")

    class Meta:
        db_table = "flow_comment"


class FlowAiChat(DataBaseModel):
    """AI 处理记录：某版本上的一次 AI 对话，回复可落为新版本。"""
    id = CharField(max_length=32, primary_key=True)
    flow_id = CharField(max_length=32, null=False, index=True)
    version_id = CharField(max_length=32, null=False, index=True, help_text="输入版本 id")
    output_version_id = CharField(max_length=32, null=False, default="", help_text="产出版本 id（存为新版本后回填，可空）")
    instruction = TextField(null=False, default="", help_text="用户指令")
    response = TextField(null=False, default="", help_text="AI 回复全文")
    session_id = CharField(max_length=64, null=False, default="", help_text="对话会话 id")

    class Meta:
        db_table = "flow_ai_chat"


class PermissionRole(DataBaseModel):
    id = CharField(max_length=32, primary_key=True, help_text="uuid")
    name = CharField(max_length=100, null=False, unique=True, help_text="角色名", index=True)
    description = TextField(null=True, help_text="角色描述")
    builtin = BooleanField(null=False, default=False, help_text="是否内置角色（内置不可删）", index=True)

    class Meta:
        db_table = "permission_role"


class PermissionRolePermission(DataBaseModel):
    id = CharField(max_length=32, primary_key=True, help_text="uuid")
    role_id = CharField(max_length=32, null=False, index=True)
    permission_key = CharField(max_length=64, null=False, index=True)

    class Meta:
        db_table = "permission_role_permission"
        indexes = ((("role_id", "permission_key"), True),)  # 联合唯一


class PermissionUserRole(DataBaseModel):
    id = CharField(max_length=32, primary_key=True, help_text="uuid")
    user_id = CharField(max_length=32, null=False, index=True)
    role_id = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "permission_user_role"
        indexes = ((("user_id", "role_id"), True),)  # 联合唯一


def alter_db_add_column(migrator, table_name, column_name, column_type):
    try:
        migrate(migrator.add_column(table_name, column_name, column_type))
    except OperationalError as ex:
        error_codes = [1060]
        error_messages = ['Duplicate column name']

        should_skip_error = (
                (hasattr(ex, 'args') and ex.args and ex.args[0] in error_codes) or
                (str(ex) in error_messages)
        )

        if not should_skip_error:
            logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, operation error: {ex}")

    except Exception as ex:
        logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, error: {ex}")
        pass

def alter_db_column_type(migrator, table_name, column_name, new_column_type):
    try:
        migrate(migrator.alter_column_type(table_name, column_name, new_column_type))
    except Exception as ex:
        logging.critical(f"Failed to alter {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name} type, error: {ex}")
        pass

def alter_db_rename_column(migrator, table_name, old_column_name, new_column_name):
    try:
        migrate(migrator.rename_column(table_name, old_column_name, new_column_name))
    except Exception:
        # rename fail will lead to a weired error.
        # logging.critical(f"Failed to rename {settings.DATABASE_TYPE.upper()}.{table_name} column {old_column_name} to {new_column_name}, error: {ex}")
        pass

def migrate_add_unique_email(migrator):
    """Deduplicates user emails and add UNIQUE constraint to email column (idempotent)"""
    # step 0: check existing index state on user.email and prepare for unique constraint
    try:
        if settings.DATABASE_TYPE.upper() == "POSTGRES":
            cursor = DB.execute_sql("""
                SELECT COUNT(*)
                FROM pg_indexes
                WHERE tablename = 'user'
                  AND indexname = 'user_email'
            """)
            result = cursor.fetchone()
            if result and result[0] > 0:
                logging.info("UNIQUE index on user.email already exists, skipping migration")
                return
        else:
            # Fetch the first index on email: tells us both the name and whether it's unique.
            # non_unique=0 means unique, non_unique=1 means non-unique.
            cursor = DB.execute_sql("""
                SELECT index_name, non_unique
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'user'
                  AND column_name = 'email'
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                index_name, non_unique = row
                if non_unique == 0:
                    logging.info("UNIQUE index on user.email already exists, skipping migration")
                    return
                # Non-unique index exists (e.g. from old peewee index=True); drop it so
                # the upcoming ADD UNIQUE INDEX does not hit MySQL error 1061 "Duplicate key name".
                DB.execute_sql(f"ALTER TABLE `user` DROP INDEX `{index_name}`")
                logging.info(f"Dropped non-unique index '{index_name}' on user.email before adding unique index")
    except Exception as ex:
        logging.warning(f"Failed to check/prepare email index on user table: {ex}, continuing with migration")

    # step 1: rename duplicate rows so the UNIQUE constraint can be applied
    try:
        duplicates = User.select(User.email).group_by(User.email).having(fn.COUNT(User.id) > 1).tuples()
        for (dup_email,) in duplicates:
            # Keep the superuser row, or the oldest row if there is no superuser
            rows = list(
                User
                    .select(User.id)
                    .where(User.email == dup_email)
                    .order_by(User.is_superuser.desc(), User.create_time.asc())
                    .tuples()
            )
            for (uid,) in rows[1:]:
                new_email = f"{dup_email}_DUPLICATE_{uid[:8]}"
                User.update(email=new_email).where(User.id == uid).execute()
                logging.warning("Renamed duplicate user %s email to %s during migration", uid, new_email)
    except Exception as ex:
        logging.critical("Failed to deduplicate user.email before adding UNIQUE constraint: %s", ex)
        return

    # step 2: add UNIQUE index via migrator
    try:
        migrate(migrator.add_index("user", ("email",), unique=True))
    except (OperationalError, ProgrammingError) as ex:
        msg = str(ex)
        # MySQL 1061 "Duplicate key name" or PostgreSQL "already exists" -> already migrated
        if "1061" in msg or "Duplicate key name" in msg or "already exists" in msg.lower():
            pass
        else:
            logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)
    except Exception as ex:
        logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)



def update_tenant_llm_to_id_primary_key():
    """Add ID and set to primary key step by step."""
    if settings.DATABASE_TYPE.upper() == "POSTGRES":
        _update_tenant_llm_to_id_primary_key_postgres()
    else:
        _update_tenant_llm_to_id_primary_key_mysql()


def _update_tenant_llm_to_id_primary_key_mysql():
    """MySQL implementation: Add ID column and set as AUTO_INCREMENT primary key."""
    try:
        with DB.atomic():
            # 0. Check if 'id' column already exists
            cursor = DB.execute_sql("""
                            SELECT COLUMN_NAME
                            FROM INFORMATION_SCHEMA.COLUMNS
                            WHERE TABLE_SCHEMA = DATABASE()
                            AND TABLE_NAME = 'tenant_llm'
                            AND COLUMN_NAME = 'id'
                        """)
            if cursor.rowcount > 0:
                return

            # 1. Add nullable column
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INT NULL")

            # 2. Set ID using MySQL user variables
            DB.execute_sql("SET @row = 0;")
            DB.execute_sql("UPDATE tenant_llm SET temp_id = (@row := @row + 1) ORDER BY tenant_id, llm_factory, llm_name;")

            # 3. Drop old primary key
            DB.execute_sql("ALTER TABLE tenant_llm DROP PRIMARY KEY")

            # 4. Update ID column to primary key with AUTO_INCREMENT
            DB.execute_sql("""
            ALTER TABLE tenant_llm
            MODIFY COLUMN temp_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY
            """)

            # 5. Add unique key
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. rename
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key.")

    except Exception as e:
        logging.error(str(e))
        cursor = DB.execute_sql("""
                                    SELECT COLUMN_NAME
                                    FROM INFORMATION_SCHEMA.COLUMNS
                                    WHERE TABLE_SCHEMA = DATABASE()
                                    AND TABLE_NAME = 'tenant_llm'
                                    AND COLUMN_NAME = 'temp_id'
                                """)
        if cursor.rowcount > 0:
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")


def _update_tenant_llm_to_id_primary_key_postgres():
    """PostgreSQL implementation: Add SERIAL primary key column to tenant_llm."""
    try:
        with DB.atomic():
            # 0. Check if 'id' column already exists
            cursor = DB.execute_sql("""
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_catalog = current_database()
                            AND table_name = 'tenant_llm'
                            AND column_name = 'id'
                        """)
            if cursor.rowcount > 0:
                return

            # 1. Add nullable integer column
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INTEGER NULL")

            # 2. Assign sequential row numbers ordered consistently
            DB.execute_sql("""
                UPDATE tenant_llm
                SET temp_id = subq.rn
                FROM (
                    SELECT ctid,
                           ROW_NUMBER() OVER (ORDER BY tenant_id, llm_factory, llm_name) AS rn
                    FROM tenant_llm
                ) AS subq
                WHERE tenant_llm.ctid = subq.ctid
            """)

            # 3. Drop old composite primary key constraint
            cursor = DB.execute_sql("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_catalog = current_database()
                  AND table_name = 'tenant_llm'
                  AND constraint_type = 'PRIMARY KEY'
            """)
            row = cursor.fetchone()
            if row:
                DB.execute_sql(f'ALTER TABLE tenant_llm DROP CONSTRAINT "{row[0]}"')

            # 4. Make temp_id NOT NULL and create a sequence for it
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET NOT NULL")
            DB.execute_sql("CREATE SEQUENCE IF NOT EXISTS tenant_llm_id_seq")
            DB.execute_sql("""
                SELECT setval('tenant_llm_id_seq', COALESCE((SELECT MAX(temp_id) FROM tenant_llm), 0))
            """)
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET DEFAULT nextval('tenant_llm_id_seq')")
            DB.execute_sql("ALTER SEQUENCE tenant_llm_id_seq OWNED BY tenant_llm.temp_id")
            DB.execute_sql("ALTER TABLE tenant_llm ADD PRIMARY KEY (temp_id)")

            # 5. Add unique constraint
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. Rename temp_id to id
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key (PostgreSQL).")

    except Exception as e:
        logging.error(str(e))
        cursor = DB.execute_sql("""
                                    SELECT column_name
                                    FROM information_schema.columns
                                    WHERE table_catalog = current_database()
                                    AND table_name = 'tenant_llm'
                                    AND column_name = 'temp_id'
                                """)
        if cursor.rowcount > 0:
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")


def migrate_db():
    logging.disable(logging.ERROR)
    migrator = DatabaseMigrator[settings.DATABASE_TYPE.upper()].value(DB)
    alter_db_add_column(migrator, "file", "source_type", CharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True))
    alter_db_add_column(migrator, "tenant", "rerank_id", CharField(max_length=128, null=False, default="BAAI/bge-reranker-v2-m3", help_text="default rerank model ID"))
    alter_db_add_column(migrator, "dialog", "rerank_id", CharField(max_length=128, null=False, default="", help_text="default rerank model ID"))
    alter_db_column_type(migrator, "dialog", "top_k", IntegerField(default=1024))
    alter_db_add_column(migrator, "tenant_llm", "api_key", CharField(max_length=2048, null=True, help_text="API KEY", index=True))
    alter_db_add_column(migrator, "api_token", "source", CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True))
    alter_db_add_column(migrator, "tenant", "tts_id", CharField(max_length=256, null=True, help_text="default tts model ID", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "source", CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True))
    alter_db_add_column(migrator, "task", "retry_count", IntegerField(default=0))
    alter_db_column_type(migrator, "api_token", "dialog_id", CharField(max_length=32, null=True, index=True))
    alter_db_add_column(migrator, "tenant_llm", "max_tokens", IntegerField(default=8192, index=True))
    alter_db_add_column(migrator, "api_4_conversation", "dsl", JSONField(null=True, default={}))
    alter_db_add_column(migrator, "knowledgebase", "pagerank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_token", "beta", CharField(max_length=255, null=True, index=True))
    alter_db_add_column(migrator, "task", "digest", TextField(null=True, help_text="task digest", default=""))
    alter_db_add_column(migrator, "task", "chunk_ids", LongTextField(null=True, help_text="chunk ids", default=""))
    alter_db_add_column(migrator, "conversation", "user_id", CharField(max_length=255, null=True, help_text="user_id", index=True))
    alter_db_add_column(migrator, "task", "task_type", CharField(max_length=32, null=False, default=""))
    alter_db_add_column(migrator, "task", "priority", IntegerField(default=0))
    alter_db_add_column(migrator, "user_canvas", "permission", CharField(max_length=16, null=False, help_text="me|team", default="me", index=True))
    alter_db_add_column(migrator, "user_canvas", "release", BooleanField(null=False, help_text="is released", default=False, index=True))
    alter_db_add_column(migrator, "llm", "is_tools", BooleanField(null=False, help_text="support tools", default=False))
    alter_db_add_column(migrator, "mcp_server", "variables", JSONField(null=True, help_text="MCP Server variables", default=dict))
    alter_db_rename_column(migrator, "task", "process_duation", "process_duration")
    alter_db_rename_column(migrator, "document", "process_duation", "process_duration")
    alter_db_add_column(migrator, "document", "suffix", CharField(max_length=32, null=False, default="", help_text="The real file extension suffix", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "errors", TextField(null=True, help_text="errors"))
    alter_db_add_column(migrator, "dialog", "meta_data_filter", JSONField(null=True, default={}))
    alter_db_column_type(migrator, "canvas_template", "title", JSONField(null=True, default=dict, help_text="Canvas title"))
    alter_db_column_type(migrator, "canvas_template", "description", JSONField(null=True, default=dict, help_text="Canvas description"))
    alter_db_add_column(migrator, "user_canvas", "canvas_category", CharField(max_length=32, null=False, default="agent_canvas", help_text="agent_canvas|dataflow_canvas", index=True))
    alter_db_add_column(migrator, "canvas_template", "canvas_category", CharField(max_length=32, null=False, default="agent_canvas", help_text="agent_canvas|dataflow_canvas", index=True))
    alter_db_add_column(migrator, "canvas_template", "canvas_types", ListField(null=True, default=list, help_text="Canvas types"))
    alter_db_add_column(migrator, "knowledgebase", "pipeline_id", CharField(max_length=32, null=True, help_text="Pipeline ID", index=True))
    alter_db_add_column(migrator, "document", "pipeline_id", CharField(max_length=32, null=True, help_text="Pipeline ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "graphrag_task_id", CharField(max_length=32, null=True, help_text="Gragh RAG task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "raptor_task_id", CharField(max_length=32, null=True, help_text="RAPTOR task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "graphrag_task_finish_at", DateTimeField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "raptor_task_finish_at", CharField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "mindmap_task_id", CharField(max_length=32, null=True, help_text="Mindmap task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "mindmap_task_finish_at", CharField(null=True))
    alter_db_column_type(migrator, "tenant_llm", "api_key", TextField(null=True, help_text="API KEY"))
    alter_db_add_column(migrator, "tenant_llm", "status", CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True))
    alter_db_add_column(migrator, "connector2kb", "auto_parse", CharField(max_length=1, null=False, default="1", index=False))
    alter_db_add_column(migrator, "llm_factories", "rank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_4_conversation", "name", CharField(max_length=255, null=True, help_text="conversation name", index=False))
    alter_db_add_column(migrator, "api_4_conversation", "exp_user_id", CharField(max_length=255, null=True, help_text="exp_user_id", index=True))
    # Migrate system_settings.value from CharField to TextField for longer sandbox configs
    alter_db_column_type(migrator, "system_settings", "value", TextField(null=False, help_text="Configuration value (JSON, string, etc.)"))
    alter_db_add_column(migrator, "document", "content_hash", CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True))
    update_tenant_llm_to_id_primary_key()
    alter_db_add_column(migrator, "tenant", "tenant_llm_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_embd_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_asr_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_img2txt_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_rerank_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_tts_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "knowledgebase", "tenant_embd_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "dialog", "tenant_llm_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "dialog", "tenant_rerank_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "memory", "tenant_embd_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "memory", "tenant_llm_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "user_canvas_version", "release", BooleanField(null=False, help_text="is released", default=False, index=True))
    alter_db_add_column(migrator, "api_4_conversation", "version_title", CharField(max_length=255, null=True, help_text="canvas version title when session created", index=False))
    alter_db_add_column(migrator, "scheduled_task", "target_url", TextField(null=True, help_text="crawl target URL", default=""))
    alter_db_add_column(migrator, "scheduled_task", "llm_id", CharField(max_length=64, null=True, help_text="LLM factory for image analysis", default=""))
    alter_db_add_column(migrator, "scheduled_task", "llm_model_name", CharField(max_length=128, null=True, help_text="LLM model name for image analysis", default=""))
    alter_db_add_column(migrator, "scheduled_task", "kb_id", CharField(max_length=32, null=True, index=True, help_text="target knowledge base ID", default=""))
    alter_db_add_column(migrator, "scheduled_task", "access_token", TextField(null=True, help_text="access token for authenticated crawling", default=""))
    alter_db_add_column(migrator, "collaboration_document", "permission", CharField(max_length=16, null=False, help_text="me|team", default="me", index=True))
    alter_db_add_column(migrator, "collaboration_format_rule", "permission", CharField(max_length=16, null=False, help_text="me|team", default="me", index=True))
    alter_db_add_column(migrator, "collaboration_document", "folder_id", CharField(max_length=32, null=True, index=True, help_text="parent folder id"))
    alter_db_add_column(migrator, "collaboration_document", "sort_order", IntegerField(default=0, help_text="sort order within folder"))
    alter_db_add_column(migrator, "collaboration_document", "ydoc", BlobField(null=True, help_text="Yjs binary state for real-time sync"))
    alter_db_add_column(migrator, "collaboration_document", "version", IntegerField(default=0, help_text="monotonic version for snapshot history"))
    CollaborationDocumentACL.create_table(safe=True)
    CollaborationComment.create_table(safe=True)
    CollaborationShareLink.create_table(safe=True)
    CollaborationAttachment.create_table(safe=True)
    CollaborationAuditLog.create_table(safe=True)
    CollaborationDocumentVersion.create_table(safe=True)
    alter_db_column_type(migrator, "document", "size", BigIntegerField(default=0, index=True))
    alter_db_column_type(migrator, "file", "size", BigIntegerField(default=0, index=True))
    alter_db_add_column(migrator, "bid_project", "fetched_at", DateTimeField(null=True, help_text="搜索缓存获取时间"))
    alter_db_add_column(migrator, "bid_project", "cache_expires_at", DateTimeField(null=True, help_text="缓存过期时间"))
    # bid_enterprise_cache table — ensure id column has AUTO_INCREMENT
    if BidEnterpriseCache.table_exists():
        try:
            # Check if the table has a working AUTO_INCREMENT by inserting a test row
            test_ok = True
            try:
                DB.execute_sql(
                    "INSERT INTO bid_enterprise_cache (company_name, cache_type, page_no, page_size, response_json, fetched_at, cache_expires_at, created_at) "
                    "VALUES ('__migration_test__', 'profile', 99, 99, '{}', NOW(), NOW(), NOW())"
                )
                test_id = DB.execute_sql("SELECT LAST_INSERT_ID()").fetchone()[0]
                DB.execute_sql("DELETE FROM bid_enterprise_cache WHERE company_name = '__migration_test__'")
                logging.info("bid_enterprise_cache AUTO_INCREMENT test OK, last_insert_id=%s", test_id)
            except Exception as e:
                test_ok = False
                logging.warning("bid_enterprise_cache AUTO_INCREMENT test failed: %s", e)
            if not test_ok:
                logging.warning("bid_enterprise_cache: dropping and recreating table (cache data loss is acceptable)")
                BidEnterpriseCache.drop_table(safe=True)
                BidEnterpriseCache.create_table(safe=True)
                logging.info("bid_enterprise_cache: table recreated with AUTO_INCREMENT")
        except Exception as e:
            logging.error("bid_enterprise_cache migration error: %s", e)
    else:
        BidEnterpriseCache.create_table(safe=True)
        logging.info("bid_enterprise_cache: table created")
    # bid_construction_project table
    if not BidConstructionProject.table_exists():
        BidConstructionProject.create_table(safe=True)
        logging.info("bid_construction_project: table created")
    # bid_construction_parse table
    if not BidConstructionParse.table_exists():
        BidConstructionParse.create_table(safe=True)
        logging.info("bid_construction_parse: table created")
    # bid_contract_parse table
    if not BidContractParse.table_exists():
        BidContractParse.create_table(safe=True)
        logging.info("bid_contract_parse: table created")
    # bid_enterprise_parse table
    if not BidEnterpriseParse.table_exists():
        BidEnterpriseParse.create_table(safe=True)
        logging.info("bid_enterprise_parse: table created")
    # bid_enterprise_business table
    if not BidEnterpriseBusiness.table_exists():
        BidEnterpriseBusiness.create_table(safe=True)
        logging.info("bid_enterprise_business: table created")
    # bid_tender_search table
    if not BidTenderSearch.table_exists():
        BidTenderSearch.create_table(safe=True)
        logging.info("bid_tender_search: table created")

    # ── 智能采集（新系统）扩展表 ──────────────────────────────────────────
    # CrawlerResult 加 category 列（老库自动加列，默认 bid 保证历史数据兼容）
    alter_db_add_column(
        migrator, "crawler_result", "category",
        CharField(max_length=32, null=False, default="bid",
                   help_text="bid|policy|personnel|news|other", index=True),
    )
    # CrawlerResult 加 site_display 列（展示用 "中文名称 域名" 拼接串，YAML 派生）
    alter_db_add_column(
        migrator, "crawler_result", "site_display",
        CharField(max_length=256, null=True, default="", index=True,
                   help_text="展示用站点串: '{中文名称} {域名}'"),
    )
    # 政策法规扩展表（新表，IF NOT EXISTS 自动创建）
    if not CollectionPolicyExt.table_exists():
        CollectionPolicyExt.create_table(safe=True)
        logging.info("collection_policy_ext: table created")
    # 人员信息扩展表
    if not CollectionPersonnelExt.table_exists():
        CollectionPersonnelExt.create_table(safe=True)
        logging.info("collection_personnel_ext: table created")
    # 异议结果扩展表
    if not CollectionObjectionExt.table_exists():
        CollectionObjectionExt.create_table(safe=True)
        logging.info("collection_objection_ext: table created")
    # 重点公开事项扩展表
    if not CollectionZdgksxmlExt.table_exists():
        CollectionZdgksxmlExt.create_table(safe=True)
        logging.info("collection_zdgksxml_ext: table created")
    # ── 智能采集通知系统（新表） ──────────────────────────────────
    if not Notification.table_exists():
        Notification.create_table(safe=True)
        logging.info("notification: table created")
    if not NotificationUser.table_exists():
        NotificationUser.create_table(safe=True)
        logging.info("notification_user: table created")
    if not NotificationSubscription.table_exists():
        NotificationSubscription.create_table(safe=True)
        logging.info("notification_subscription: table created")
    # ── 权限管控 RBAC（新表 + 初始 seed）─────────────
    if not PermissionRole.table_exists():
        PermissionRole.create_table(safe=True)
        logging.info("permission_role: table created")
    if not PermissionRolePermission.table_exists():
        PermissionRolePermission.create_table(safe=True)
        logging.info("permission_role_permission: table created")
    if not PermissionUserRole.table_exists():
        PermissionUserRole.create_table(safe=True)
        logging.info("permission_user_role: table created")
    seed_default_permissions()

    logging.disable(logging.NOTSET)
    # this is after re-enabling logging to allow logging changed user emails
    migrate_add_unique_email(migrator)


def seed_default_permissions():
    """幂等写入内置角色：超级管理员 + 普通用户（含默认权限点）。"""
    try:
        from common.misc_utils import get_uuid

        # 内置超级管理员
        super_role = PermissionRole.get_or_none(PermissionRole.name == SUPER_ROLE_NAME)
        if not super_role:
            super_role = PermissionRole.create(
                id=get_uuid(),
                name=SUPER_ROLE_NAME,
                description="内置超级管理员，默认拥有全部模块权限（is_superuser 亦直接放行）",
                builtin=True,
            )
            logging.info("permission_role: 创建内置【%s】", SUPER_ROLE_NAME)
        # 内置普通用户
        normal_role = PermissionRole.get_or_none(PermissionRole.name == NORMAL_ROLE_NAME)
        if not normal_role:
            normal_role = PermissionRole.create(
                id=get_uuid(),
                name=NORMAL_ROLE_NAME,
                description="内置普通用户，默认授予基础模块",
                builtin=True,
            )
            logging.info("permission_role: 创建内置【%s】", NORMAL_ROLE_NAME)
        # 普通用户默认权限点（幂等）
        for key in NORMAL_ROLE_PERMISSIONS:
            exists = PermissionRolePermission.get_or_none(
                role_id=normal_role.id, permission_key=key
            )
            if not exists:
                PermissionRolePermission.create(
                    id=get_uuid(),
                    role_id=normal_role.id,
                    permission_key=key,
                )
        # 存量回填：未配置任何角色的用户默认挂「普通用户」（幂等，每次启动执行）。
        # 含超管：is_superuser 权限直通，挂普通角色仅为权限页展示/统计一致。
        # 权限缓存无需失效：无角色用户的权限判定本就回退到普通角色权限点，回填前后结果一致。
        # 注意：不能用 SQL 跨表子查询（NOT IN (SELECT user_id ...)）——
        # user 表与 permission_user_role 表排序规则可能不一致，MySQL 报 1267 Illegal mix of collations，
        # 故在 Python 侧做集合差。
        having_role_ids = {r.user_id for r in PermissionUserRole.select(PermissionUserRole.user_id)}
        backfilled = 0
        for u in User.select(User.id):
            if u.id in having_role_ids:
                continue
            try:
                PermissionUserRole.create(id=get_uuid(), user_id=u.id, role_id=normal_role.id)
                backfilled += 1
            except Exception as row_err:
                logging.warning("backfill normal role failed for user %s: %s", u.id, row_err)
        if backfilled:
            logging.info("permission_user_role: 存量回填【%s】角色 %d 个用户", NORMAL_ROLE_NAME, backfilled)
        # 超级管理员不列为具体权限点（逻辑上视为全通过即可），这里不写入。
    except Exception as e:
        logging.error("seed_default_permissions failed: %s", e)
