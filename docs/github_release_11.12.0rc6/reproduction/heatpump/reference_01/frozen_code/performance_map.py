"""Exact catalogue-copy admission and conservative heating lookup, not a field model.

Only the identified 24-page TICA brochure is admitted. Q and P are separately
interpolated on adjacent printed axes. Blank corners and extrapolation fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re

import fitz
import pandas as pd

PDF_SHA256 = '5be3093936a6ec3bb7104372395f8f5c6b375563e40ded024331b0f77e3854a0'
SOURCE_ID = 'TICA_VForce_24p_sha5be309_p15_heating'
SOURCE_URL = 'https://dec.com.pk/assets/images/products/tica/3-Inverter-modular-chiller.pdf'
MODELS = ('TCAV035BHE', 'TCAV065BHE')
AMBIENT = (-26., -20., -15., -10., -5., 0., 7., 10., 15., 20., 25., 30., 35., 48., 55.)
WATER = (30., 35., 40., 45., 50., 55.)
CONDITION_ID = 'heating_single_module_catalogue_p15_ambient_LWT_WB_unspecified'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def decode_pdf_number(text):
    # This hash-specific PDF embeds digit glyphs shifted by 29 in ArialMT.
    decoded = ''.join(chr(ord(c) + 29) if ord(c) < 32 else c for c in text).strip()
    return float(decoded) if re.fullmatch(r'-?\d+(?:\.\d+)?', decoded) else None


def extract_heating_grid(pdf):
    require(sha(pdf) == PDF_SHA256, 'unrecognized catalogue version: source SHA mismatch')
    with fitz.open(pdf) as document:
        require(len(document) == 24, 'wrong source page count')
        spans = [s for b in document[14].get_text('dict')['blocks'] if 'lines' in b
                 for line in b['lines'] for s in line['spans']]
    records = []
    # Geometric rows/columns retain printed blank cells rather than collapsing them.
    for model, y_header, row_y, x0, step in [
        (MODELS[0], 525.0, (557.4, 569.9, 582.4, 595.1, 607.9, 620.6), 74.95, 32.365),
        (MODELS[1], 663.2, (695.6, 708.1, 720.6, 733.3, 746.1, 758.8), 74.6, 32.395),
    ]:
        model_text = [s['text'] for s in spans if model == s['text'] and abs(s['bbox'][1]-(y_header-27))<3]
        require(len(model_text) == 1, 'missing or duplicate printed model')
        headers = sorted((s for s in spans if abs(s['bbox'][1] - y_header) < .4 and s['bbox'][0] > 65), key=lambda s:s['bbox'][0])
        require(tuple(decode_pdf_number(s['text']) for s in headers) == AMBIENT, 'ambient header mismatch')
        for water, y in zip(WATER, row_y):
            line = [s for s in spans if abs(s['bbox'][1] - y) < .4]
            water_tokens = [s for s in line if s['bbox'][0] < 65]
            require(len(water_tokens) == 1 and decode_pdf_number(water_tokens[0]['text']) == water, 'water header mismatch')
            for j, ambient in enumerate(AMBIENT):
                capacity = [s for s in line if abs((s['bbox'][0]+s['bbox'][2])/2 - (x0+step*j)) < 1.2]
                power = [s for s in line if abs((s['bbox'][0]+s['bbox'][2])/2 - (x0+step*j+16.18)) < 1.2]
                require(len(capacity) == len(power) and len(capacity) <= 1, 'unpaired/ambiguous PDF cell')
                if not capacity:
                    continue
                q, p = decode_pdf_number(capacity[0]['text']), decode_pdf_number(power[0]['text'])
                require(q is not None and p is not None and q > 0 and p > 0, 'nonpositive or unreadable PDF cell')
                records.append(dict(source_id=SOURCE_ID, source_sha256=PDF_SHA256, condition_id=CONDITION_ID,
                    model=model, ambient_c=ambient, leaving_water_c=water, q_kw=q, p_kw=p, cop_derived=q/p,
                    q_unit='kW', p_unit='kW', pdf_page=15, printed_page=13,
                    capacity_bbox=','.join(f'{v:.5f}' for v in capacity[0]['bbox']),
                    power_bbox=','.join(f'{v:.5f}' for v in power[0]['bbox']),
                    evidence_kind='manufacturer_catalogue_sample_not_measurement',
                    wet_bulb_c='unspecified', defrost_integration='unspecified'))
    require(pd.DataFrame(records).groupby('model').size().to_dict() == {MODELS[0]:83, MODELS[1]:82}, 'unexpected grid size')
    return pd.DataFrame(records)


def compare_user_csv(grid, csv):
    old = pd.read_csv(csv, encoding='utf-8-sig', dtype=str).fillna('')
    chosen = old[(old['型号'].isin(MODELS)) & old['来源文件'].str.contains('p.15', regex=False)]
    require(len(chosen) == 165, 'expected exactly 165 catalogue CSV cells')
    lookup = {}
    for line, row in chosen.iterrows():
        require(row['数据性质(样本/检测报告/能效备案/招标门槛)'] == '样本', 'non-catalogue evidence rejected')
        require(row['source_url'] == SOURCE_URL, 'CSV point source URL changed')
        key = row['型号'], float(row['源侧温度_C']), float(row['出水温度_C'])
        require(key not in lookup, 'duplicate user CSV cell')
        lookup[key] = row, int(line)+2
    result = []
    for row in grid.to_dict('records'):
        key = row['model'], row['ambient_c'], row['leaving_water_c']
        require(key in lookup, 'PDF cell absent from CSV')
        oldrow, line = lookup[key]
        match = float(oldrow['制热量_kW']) == row['q_kw'] and float(oldrow['输入功率_kW']) == row['p_kw']
        require(match, f'PDF/CSV Q/P mismatch at {key}')
        require(oldrow['COP'] == '' or abs(float(oldrow['COP'])-row['cop_derived']) <= .0051, 'inconsistent asserted COP')
        result.append(dict(model=key[0], ambient_c=key[1], leaving_water_c=key[2], user_csv_line=line,
                           pdf_q_kw=row['q_kw'], pdf_p_kw=row['p_kw'], q_p_exact_match=match, old_cop_blank=oldrow['COP']==''))
    return pd.DataFrame(result)


@dataclass(frozen=True)
class Performance:
    q_kw: float
    p_kw: float
    cop: float
    corners: tuple


class CatalogueMap:
    def __init__(self, frame):
        columns = ['source_id', 'source_sha256', 'condition_id', 'model', 'ambient_c', 'leaving_water_c', 'q_kw', 'p_kw', 'q_unit', 'p_unit']
        require(all(x in frame for x in columns) and len(frame)>0, 'missing map fields')
        require(set(frame.source_id)=={SOURCE_ID} and set(frame.source_sha256)=={PDF_SHA256} and set(frame.condition_id)=={CONDITION_ID}, 'cross-source/condition mapping rejected')
        require(set(frame.model) <= set(MODELS), 'unknown catalogue model, no suffix aliases')
        require(set(frame.q_unit)=={'kW'} and set(frame.p_unit)=={'kW'}, 'unit mismatch')
        self.points = {}
        for r in frame.to_dict('records'):
            key = (r['model'], float(r['ambient_c']), float(r['leaving_water_c']))
            q, p = float(r['q_kw']), float(r['p_kw'])
            require(key[1] in AMBIENT and key[2] in WATER, 'unprinted map coordinates')
            require(math.isfinite(q) and math.isfinite(p) and q>0 and p>0, 'invalid Q/P')
            require(key not in self.points, 'duplicate map cell')
            self.points[key] = q, p

    @staticmethod
    def bracket(value, axis):
        require(math.isfinite(value), 'nonfinite query')
        require(axis[0] <= value <= axis[-1], 'outside printed axis; extrapolation rejected')
        if value in axis:
            return ((value, 1.),)
        for a, b in zip(axis[:-1], axis[1:]):
            if a < value < b:
                w = (value-a)/(b-a)
                return ((a, 1-w), (b, w))
        raise ValueError('unbracketed query')

    def query(self, model, ambient_c, leaving_water_c):
        require(model in MODELS, 'unknown catalogue model, no suffix aliases')
        xs, ys = self.bracket(float(ambient_c), AMBIENT), self.bracket(float(leaving_water_c), WATER)
        q = p = 0.
        corners = []
        for x, wx in xs:
            for y, wy in ys:
                key = model, x, y
                require(key in self.points, f'blank printed corner {key}; interpolation rejected')
                qi, pi = self.points[key]; w = wx*wy
                q += w*qi; p += w*pi
                corners.append((x,y,w))
        return Performance(q, p, q/p, tuple(corners))
