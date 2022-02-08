import dataclasses
import psycopg2
import psycopg2.sql
import psycopg2.extras
import contextlib
import datetime
import abc


@dataclasses.dataclass(frozen=True)
class MonitorEvent:
    chat_id: int
    start_time: datetime.datetime
    end_time: datetime.datetime
    restaurant_name: str
    restaurant_opened: bool


@dataclasses.dataclass(frozen=True)
class GeneralStats:
    bot_usage_count: int
    most_popular_restaurant: str
    most_popular_restaurant_request_count: int
    most_popular_restaurant_unique_chat_count: int
    slowest_restaurant: str
    slowest_restaurant_average_wait_time: datetime.timedelta

    def pretty_print(self):
        return "\n".join([
            f"Bot was used {self.bot_usage_count} times.",
            f'The most popular restaurant is "{self.most_popular_restaurant}", '
            f"it was waited on {self.most_popular_restaurant_request_count} times, "
            f"by {self.most_popular_restaurant_unique_chat_count} different people.",
            f'The slowest restaurant is "{self.slowest_restaurant}". '
            f'On average, people wait {self.slowest_restaurant_average_wait_time} for it to open.'
        ])


@dataclasses.dataclass(frozen=True)
class RestaurantStats:
    average_wait_time: datetime.timedelta


@dataclasses.dataclass(frozen=True)
class ChatStats:
    bot_usage_count: int
    most_popular_restaurant: str
    total_waiting_time: datetime.timedelta


class StatsInterface(abc.ABC):
    def setup(self):
        pass

    @abc.abstractmethod
    def report_monitor_events(self, events: list[MonitorEvent]):
        pass

    @abc.abstractmethod
    def get_chat_stats(self, chat_id) -> ChatStats:
        pass

    @abc.abstractmethod
    def get_general_stats(self) -> GeneralStats:
        pass

    @abc.abstractmethod
    def get_restaurant_stats(self, restaurant_name) -> RestaurantStats:
        pass


class PostgresStats(StatsInterface):
    def __init__(self,
                 connection_pool: psycopg2.pool.AbstractConnectionPool,
                 table_name):
        self._pool = connection_pool
        self._table_name = psycopg2.sql.Identifier(table_name)
        self._view_name = psycopg2.sql.Identifier(table_name + "_view")

    @contextlib.contextmanager
    def _get_connection(self):
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def setup(self):
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                query = psycopg2.sql.SQL("""
                CREATE TABLE IF NOT EXISTS {0} (
                    chat_id bigint NOT NULL,
                    start_time timestamp,
                    end_time timestamp,
                    restaurant_name text NOT NULL,
                    restaurant_opened boolean
                );
                CREATE MATERIALIZED VIEW IF NOT EXISTS {1} AS
                    SELECT a.restaurant_name,
                           a.request_count,
                           a.unique_chat_count,
                           a.total_wait_time,
                           b.average_wait_time
                     FROM (SELECT restaurant_name,
                                  count(*) AS request_count,
                                  count(DISTINCT chat_id) AS unique_chat_count,
                                  sum(end_time-start_time) AS total_wait_time
                             FROM {0}
                         GROUP BY restaurant_name) AS a
               INNER JOIN (SELECT restaurant_name,
                                  avg(end_time-start_time) AS average_wait_time
                             FROM {0}
                            WHERE restaurant_opened = true
                            GROUP BY restaurant_name) AS b
                     ON a.restaurant_name = b.restaurant_name;
                """).format(self._table_name, self._view_name)

                cur.execute(query)

            conn.commit()

    def get_general_stats(self) -> GeneralStats:
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                query = psycopg2.sql.SQL("""
                SELECT restaurant_name,request_count,unique_chat_count
                  FROM {}
                 ORDER BY request_count DESC
                 LIMIT 1;
                """).format(self._view_name)

                cur.execute(query)

                query_result = cur.fetchone()
                if query_result == None:
                    return

                most_popular_restaurant, request_count, unique_chat_count = query_result

                query = psycopg2.sql.SQL("""
                SELECT restaurant_name,average_wait_time
                  FROM {}
                  ORDER BY average_wait_time DESC
                  LIMIT 1
                """).format(self._view_name)

                cur.execute(query)

                slowest_restaurant, average_wait_time = cur.fetchone()

                query = psycopg2.sql.SQL("SELECT count(*) FROM {}").format(self._table_name)

                cur.execute(query)
                bot_usage_count, = cur.fetchone()

                return GeneralStats(bot_usage_count,
                                    most_popular_restaurant,
                                    request_count,
                                    unique_chat_count,
                                    slowest_restaurant,
                                    average_wait_time)

    def get_chat_stats(self, chat_id) -> ChatStats:
        pass

    def get_restaurant_stats(self, restaurant_name) -> RestaurantStats:
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                query = psycopg2.sql.SQL("""
                SELECT average_wait_time
                  FROM {}
                 WHERE restaurant_name = %s
                """).format(self._view_name)

                cur.execute(query, (restaurant_name,))

                if query_result := cur.fetchone():
                    return RestaurantStats(average_wait_time=query_result[0])

                return None

    def report_monitor_events(self, events: list[MonitorEvent]):
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                query = psycopg2.sql.SQL("INSERT INTO {} VALUES %s").format(self._table_name)
                values = (dataclasses.astuple(i) for i in events)
                psycopg2.extras.execute_values(
                    cur,
                    query,
                    values)

                query = psycopg2.sql.SQL("REFRESH MATERIALIZED VIEW {}").format(self._view_name)
                cur.execute(query)

            conn.commit()