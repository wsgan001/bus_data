# -*- coding: utf-8 -*-
from peewee import *
from playhouse.sqlite_ext import SqliteExtDatabase
import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/pyshp')
import shapefile

import logging
logger = logging.getLogger('peewee')
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())

database_proxy = Proxy()  # Create a proxy for our db.


SRID = 4326


class PolygonField(Field):
    db_field = 'polygon'


class PointField(Field):
    db_field = 'point'


class LineStringField(Field):
    db_field = 'linestring'


class MultiPolygonField(Field):
    db_field = 'multipolygon'


class MultiPointField(Field):
    db_field = 'multipoint'


class MultiLineStringField(Field):
    db_field = 'multilinestring'


class RouteTable(Model):
    """
    """
    id = PrimaryKeyField()
    operationCompany = TextField(index=True)
    lineName = TextField(index=True)
    route = TextField(index=True)
    geometry = LineStringField()

    class Meta:
        database = database_proxy


class BusStop(Model):
    """
    """
    route= ForeignKeyField(RouteTable, related_name='busroute')
    stopName = TextField(index=True)
    stopNameKana = TextField()
    lat = FloatField(index=True)
    long = FloatField(index=True)

    class Meta:
        database = database_proxy


class TimeTable(Model):
    """
    """
    route= ForeignKeyField(RouteTable, related_name='timeroute')
    dateType = IntegerField()

    class Meta:
        database = database_proxy


class TimeTableItem(Model):
    """
    """
    timeTable= ForeignKeyField(TimeTable, related_name='timetable')
    busStop = ForeignKeyField(BusStop, related_name='busstop')
    time = TextField()

    class Meta:
        database = database_proxy

def connect(path, spatialite_path, evn_sep=';'):
    """
    データベースへの接続
    @param path sqliteのパス
    @param spatialite_path mod_spatialiteへのパス
    @param env_sep 環境変数PATHの接続文字 WINDOWSは; LINUXは:
    """
    os.environ["PATH"] = os.environ["PATH"] + evn_sep + os.path.dirname(spatialite_path)
    db = SqliteExtDatabase(path)
    database_proxy.initialize(db)
    db.field_overrides = {
        'polygon': 'POLYGON',
        'point': 'POINT',
        'linestring': 'LINESTRING',
        'multipolygon': 'MULTIPOLYGON',
        'multipoint': 'MULTIPOINT',
        'multilinestring': 'MULTILINESTRING',
    }
    db.load_extension(os.path.basename(spatialite_path))


def setup(path, spatialite_path, evn_sep=';'):
    connect(path, spatialite_path, evn_sep)
    database_proxy.create_tables([BusStop, TimeTable, TimeTableItem], True)

    database_proxy.get_conn().execute('SELECT InitSpatialMetaData()')
    database_proxy.get_conn().execute("""
        CREATE TABLE IF NOT EXISTS "RouteTable" (
          "id" INTEGER PRIMARY KEY AUTOINCREMENT,
          "operationCompany" TEXT,
          "lineName" TEXT ,
          "Route" TEXT);
    """)
    database_proxy.get_conn().execute("""
        CREATE INDEX IF NOT EXISTS RouteTable_operationCompany ON "RouteTable"("operationCompany");
    """)
    database_proxy.get_conn().execute("""
        CREATE INDEX IF NOT EXISTS RouteTable_lineName ON "RouteTable"("lineName");
    """)
    database_proxy.get_conn().execute("""
        Select AddGeometryColumn ("RouteTable", "Geometry", ?, "LINESTRING", 2);
    """, (SRID,))

def _import_time_table(route, bus_rows, date_type, timetables):
    for t in timetables:
        timerow = TimeTable.create(
            route = route,
            dateType = date_type
        )
        # バス停毎の到着時間
        for s in t:
            busstop = bus_rows[s['busstop']]
            TimeTableItem.create(
                 timeTable = timerow,
                 busStop = busstop,
                 time = s['time']
            )

def _makeGeometryString(type, shape):
    r = type + '('
    i = 0
    for d in shape.points:
        if i > 0:
            r += ','
        r = r + ('%f %f' % (d[0], d[1]))
        i += 1
    r += ')'
    return r

def import_bus(operation_company, line_name, shape, src_srid, timetables):
    with database_proxy.transaction():
        # 既存データの削除
        routeid = []
        query = RouteTable.select().where(
            (RouteTable.operationCompany == operation_company) &
            (RouteTable.lineName == line_name)
        ).execute()
        for row in query:
            routeid.append(row.id)

        BusStop.delete().filter(
            (BusStop.route << routeid)
        ).execute()

        query = TimeTable.select().where(
            (TimeTable.route << routeid)
        ).execute()

        timetableid = []
        for row in query:
            timetableid.append(row.id)
        TimeTableItem.delete().filter(
            TimeTableItem.timeTable << timetableid
        ).execute()

        TimeTable.delete().filter(
            (TimeTable.route << routeid)
        ).execute()

        routedict = {}
        sf = shapefile.Reader(shape)
        shaperec = sf.iterShapeRecords()
        for sr in shaperec:
            routedict[sr.record[0]] = _makeGeometryString('LINESTRING', sr.shape)

        for timetable in timetables:
            database_proxy.get_conn().execute(
                """
                INSERT INTO RouteTable
                  (operationCompany, lineName, route, geometry)
                VALUES(?,?,?,Transform(GeometryFromText(?, ?),?))
                """,
                (
                    operation_company,
                    line_name,
                    timetable['route'],
                    routedict[timetable['route']], src_srid, SRID
                )
            )
            route = RouteTable.get(
                (RouteTable.operationCompany==operation_company) &
                (RouteTable.lineName==line_name) &
                (RouteTable.route==timetable['route'])
            )
            bus_rows = {}
            for b in timetable['bus_stops']:
                row = BusStop.create(
                    route = route,
                    stopName = b['stopName'],
                    stopNameKana = b['stopNameKana'],
                    lat = b['lat'],
                    long = b['long']
                )
                bus_rows[b['stopName']] = row
            _import_time_table(route, bus_rows, 0, timetable['weekday_timetable'])
            _import_time_table(route, bus_rows, 1, timetable['saturday_timetable'])
            _import_time_table(route, bus_rows, 2, timetable['holyday_timetable'])