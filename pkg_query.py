"""
pkg_query.py — Package 정보 조회 (bigdataquery)

사용법:
    python pkg_query.py 8KC

조회 테이블:
    T1: MOS_TSP_SMI.GPM_TP_BE_MES_PKG               — 패키지 기본 정보
    T2: MOS_TSP_SMI.GPM_TP_BE_MES_ASSY_TOTAL        — 어셈블리 정보 (null 자동 보완)
    T3: MOS_TSP_SMI.GPM_TP_BE_MES_PKG_QC_INSPECTION — QC 치수 정보
    T4: MOS_TSP_SMI.GPM_TP_BE_MAT_MATS_USED         — 트레이 정보

환경변수 (.env):
    BDQ_USER=your_ssoid
    BDQ_PASS=your_ad_password
"""

import argparse
import io
import logging
import os
import sys
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── bigdataquery 로그인 ──────────────────────────────────────────

def _bdq_login() -> bool:
    try:
        import bigdataquery as bdq
    except ImportError:
        logger.error("bigdataquery 패키지가 설치되어 있지 않습니다.")
        return False

    user = os.getenv("BDQ_USER", "")
    pw   = os.getenv("BDQ_PASS", "")

    if not user or not pw:
        logger.error("BDQ_USER 또는 BDQ_PASS 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")
        return False

    account_info = io.StringIO(f"{user}\n{pw}")
    try:
        sys.stdin = account_info
        bdq.login()
        logger.info(f"bigdataquery 로그인 성공 (user: {user})")
        return True
    except Exception as exc:
        logger.error(f"bigdataquery 로그인 실패: {exc}")
        return False
    finally:
        account_info.close()
        sys.stdin = io.StringIO("")


def _get_data(sql: str) -> Optional[pd.DataFrame]:
    try:
        import bigdataquery as bdq
    except ImportError:
        return None

    user = os.getenv("BDQ_USER", "")
    logger.debug(f"getData 호출:\n{sql.strip()}")
    try:
        df = bdq.getData(param=sql, user_name=user)
        return df
    except Exception as exc:
        logger.error(f"getData 실패: {exc}")
        return None


# ── 각 테이블 조회 ───────────────────────────────────────────────

def _query_t1(pkg_code: str) -> pd.DataFrame:
    """T1: 패키지 기본 정보"""
    sql = f"""
        SELECT pkg_code, pkg_name, remark, pkg_gubun, pin
        FROM MOS_TSP_SMI.GPM_TP_BE_MES_PKG
        WHERE UPPER(pkg_code) = UPPER('{pkg_code}')
    """
    df = _get_data(sql)
    if df is None or df.empty:
        logger.warning(f"T1: pkg_code='{pkg_code}' 데이터 없음")
        return pd.DataFrame(columns=["pkg_code", "pkg_name", "remark", "pkg_gubun", "pin"])
    logger.info(f"T1: {len(df)}행 조회")
    return df


def _query_t2(pkg_code: str) -> pd.DataFrame:
    """
    T2: 어셈블리 정보
    동일 pkg_code 내 행 간 누락값을 다른 행으로 채워서 반환한다.
    """
    sql = f"""
        SELECT pkg_code, AO_PROD_CODE, tray_code, pkg_diagram, mk_diagram, lf_diagram
        FROM MOS_TSP_SMI.GPM_TP_BE_MES_ASSY_TOTAL
        WHERE UPPER(pkg_code) = UPPER('{pkg_code}')
    """
    cols = ["pkg_code", "AO_PROD_CODE", "tray_code", "pkg_diagram", "mk_diagram", "lf_diagram"]
    df = _get_data(sql)
    if df is None or df.empty:
        logger.warning(f"T2: pkg_code='{pkg_code}' 데이터 없음")
        return pd.DataFrame(columns=cols)

    logger.info(f"T2: {len(df)}행 조회 (null 채우기 전)")

    # pkg_code 그룹 내에서 각 컬럼의 첫 번째 non-null 값으로 null을 채움
    fill_cols = ["AO_PROD_CODE", "tray_code", "pkg_diagram", "mk_diagram", "lf_diagram"]
    for col in fill_cols:
        if col not in df.columns:
            continue
        df[col] = df.groupby("pkg_code")[col].transform(
            lambda x: x.fillna(method="ffill").fillna(method="bfill")
        )

    df = df.drop_duplicates()
    logger.info(f"T2: null 채우기 후 {len(df)}행")
    return df


def _query_t3(pkg_code: str) -> pd.DataFrame:
    """T3: QC 치수 정보 (파생 컬럼: component_height, body_thickness 포함)"""
    sql = f"""
        SELECT
            pkg_code,
            pkg_hgt_std_vals + package_height_tolerence  AS component_height,
            pkg_hgt_std_vals - solder_ball_height        AS body_thickness,
            coplanarity,
            solder_ball_size,
            solder_ball_size_post_reflow                 AS ball_width_post_reflow,
            solder_ball_height                           AS ball_height,
            solder_ball_height_tolerence
        FROM MOS_TSP_SMI.GPM_TP_BE_MES_PKG_QC_INSPECTION
        WHERE UPPER(pkg_code) = UPPER('{pkg_code}')
    """
    cols = [
        "pkg_code", "component_height", "body_thickness", "coplanarity",
        "solder_ball_size", "ball_width_post_reflow", "ball_height",
        "solder_ball_height_tolerence",
    ]
    df = _get_data(sql)
    if df is None or df.empty:
        logger.warning(f"T3: pkg_code='{pkg_code}' 데이터 없음")
        return pd.DataFrame(columns=cols)
    logger.info(f"T3: {len(df)}행 조회")
    return df


def _query_t4(tray_codes: list) -> pd.DataFrame:
    """T4: 트레이 정보 — T2에서 추출한 tray_code 목록으로 조회"""
    cols = ["tray_code", "tray_diagram", "N1", "N2", "X", "Y", "DX", "DY"]
    if not tray_codes:
        return pd.DataFrame(columns=cols)

    codes_str = ", ".join(f"'{c}'" for c in tray_codes)
    sql = f"""
        SELECT
            piece_part_no     AS tray_code,
            material_spec     AS tray_diagram,
            TR_array_y        AS N1,
            TR_array_x        AS N2,
            tr_end_pitch_y    AS X,
            tr_end_pitch_x    AS Y,
            tr_pocket_pitch_y AS DX,
            tr_pocket_pitch_x AS DY
        FROM MOS_TSP_SMI.GPM_TP_BE_MAT_MATS_USED
        WHERE piece_part_no IN ({codes_str})
    """
    df = _get_data(sql)
    if df is None or df.empty:
        logger.warning(f"T4: tray_codes={tray_codes} 데이터 없음")
        return pd.DataFrame(columns=cols)
    logger.info(f"T4: {len(df)}행 조회")
    return df


# ── 메인 조회 함수 ───────────────────────────────────────────────

def query_package(pkg_code: str) -> dict:
    """
    pkg_code 기준으로 4개 테이블을 순서대로 조회해 결과를 반환한다.

    반환값:
        {
            "pkg_info":  DataFrame — T1 패키지 기본 정보
            "assy_info": DataFrame — T2 어셈블리 정보 (null 보완됨)
            "qc_info":   DataFrame — T3 QC 치수 정보
            "tray_info": DataFrame — T4 트레이 정보
        }
    """
    logger.info(f"=== pkg_code='{pkg_code.upper()}' 조회 시작 ===")

    if not _bdq_login():
        raise RuntimeError("bigdataquery 로그인 실패. BDQ_USER/BDQ_PASS를 확인하세요.")

    t1 = _query_t1(pkg_code)
    t2 = _query_t2(pkg_code)
    t3 = _query_t3(pkg_code)

    tray_codes = t2["tray_code"].dropna().unique().tolist() if not t2.empty else []
    t4 = _query_t4(tray_codes)

    logger.info(f"=== pkg_code='{pkg_code.upper()}' 조회 완료 ===")

    return {
        "pkg_info":  t1,
        "assy_info": t2,
        "qc_info":   t3,
        "tray_info": t4,
    }


# ── 출력 ─────────────────────────────────────────────────────────

def print_result(result: dict) -> None:
    pd.set_option("display.max_columns", None)
    pd.set_option("display.max_colwidth", 40)
    pd.set_option("display.width", 220)

    sections = [
        ("pkg_info",  "T1  패키지 기본 정보"),
        ("assy_info", "T2  어셈블리 정보"),
        ("qc_info",   "T3  QC 치수 정보"),
        ("tray_info", "T4  트레이 정보"),
    ]

    for key, title in sections:
        df = result.get(key, pd.DataFrame())
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
        if df.empty:
            print("  (데이터 없음)")
        else:
            print(df.to_string(index=False))
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Package 정보 조회 — bigdataquery")
    parser.add_argument("pkg_code", help="조회할 패키지 코드 (예: 8KC)")
    args = parser.parse_args()

    try:
        result = query_package(args.pkg_code)
        print_result(result)
    except Exception as e:
        logger.error(f"조회 실패: {e}")
        sys.exit(1)
