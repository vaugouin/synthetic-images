#!/usr/bin/env python
"""
synthetic-images -- batch driver for Project A ("An image for everything").

Two modes:
  * SINGLE ITEM (the dev/test harness, review §2):
        python synthetic-images.py --item-wikidata Q183 --class location --representation flag --dry-run
        python synthetic-images.py --item-id 5 --class technical --dry-run
  * BATCH over a class at a usage threshold (review §11 phasing):
        python synthetic-images.py --class technical --dry-run
        python synthetic-images.py --class technical --limit 10
  * LOGO PADDING ONLY (decision #7; zero model cost -- companies/networks default to the
    'logo-pad' representation, which routes to the deterministic Pillow path):
        python synthetic-images.py --class company
        python synthetic-images.py --class network

Guards (review §6): --dry-run (no API, no DB writes), --limit, and a per-run USD budget
ceiling (RUN_BUDGET_USD). Idempotency/resume is handled per item in the pipeline (skip unless
--force). Concurrency is left at 1 here on purpose -- the shared DB connection is single-thread
(citizenphil); parallelise the network/render work later, keep DB writes on one thread.

Phase 1 implements Technicals end-to-end (review §11). Other classes have a generic selector
stub with a clear TODO so the vertical slice ships before the corpus fans out.
"""
import os
import sys
import argparse

import citizenphil as cp
import synthetic_images_functions as si

strsqlns = os.environ.get("DB_NAMESPACE", "T_WC_")
dblbudget = float(os.environ.get("RUN_BUDGET_USD", "20"))
lngthresholddefault = int(os.environ.get("USAGE_THRESHOLD", "5"))


# ---------------------------------------------------------------------------
# Representation selection (reads the controlled vocabulary, review §5/§9)
# ---------------------------------------------------------------------------
def f_choose_representation(stritemclass, strinstanceof="", stroverride=""):
    """
    Pick the representation for an item: explicit override > INSTANCE_OF rule match >
    class default (IS_CHOSEN_DEFAULT) > 'plate'. Mirrors the §4.2 INSTANCE_OF -> representation
    lever using T_WC_T2S_REPRESENTATION.INSTANCE_OF_RULE.
    """
    if stroverride:
        return stroverride
    if strinstanceof:
        strrep = cp.f_fieldfromquery(
            "SELECT REPRESENTATION FROM " + strsqlns + "T2S_REPRESENTATION "
            "WHERE ITEM_CLASS=%s AND INSTANCE_OF_RULE LIKE %s "
            "AND (DELETED IS NULL OR DELETED=0) ORDER BY FALLBACK_ORDER LIMIT 1",
            "REPRESENTATION", params=(stritemclass, "%" + strinstanceof + "%"),
        )
        if strrep:
            return strrep
    strdefault = cp.f_fieldfromquery(
        "SELECT REPRESENTATION FROM " + strsqlns + "T2S_REPRESENTATION "
        "WHERE ITEM_CLASS=%s AND IS_CHOSEN_DEFAULT=1 AND (DELETED IS NULL OR DELETED=0) LIMIT 1",
        "REPRESENTATION", params=(stritemclass,),
    )
    return strdefault or "plate"


# ---------------------------------------------------------------------------
# Entity selection per class
# ---------------------------------------------------------------------------
def f_select_technicals(lnglimit=0):
    """Phase-1 class: Technicals (73 rows, has Wikidata, the only class with real sub-types)."""
    # Name = WIKIDATA_LABEL, falling back to DESCRIPTION (mirrors lib/technical.inc.php).
    strsql = (
        "SELECT ID_TECHNICAL AS id_item, ID_WIKIDATA AS id_wikidata, "
        "COALESCE(NULLIF(WIKIDATA_LABEL,''), DESCRIPTION) AS name, "
        "OVERVIEW AS overview, TECHNICAL_TYPE AS instance_of "
        "FROM " + strsqlns + "T2S_TECHNICAL "
        "WHERE (DELETED IS NULL OR DELETED=0) ORDER BY POPULARITY DESC"
    )
    if lnglimit:
        strsql += " LIMIT {0}".format(int(lnglimit))
    return _fetchall(strsql)


def f_select_companies(lnglimit=0):
    """
    Decision-#7 class: companies pad their REAL TMDb logo (no synthesis). The LOGO_PATH filter
    IS the v1 scope: only the ~19k rows that have a logo are selected; the ~156k logo-less
    companies are a separate threshold/skip decision (review §4.1) and never enter the run.
    """
    strsql = (
        "SELECT ID_COMPANY AS id_item, COMPANY_NAME AS name, LOGO_PATH AS logo_path "
        "FROM " + strsqlns + "T2S_COMPANY "
        "WHERE LOGO_PATH IS NOT NULL AND LOGO_PATH<>'' AND (DELETED IS NULL OR DELETED=0) "
        "ORDER BY POPULARITY DESC"
    )
    if lnglimit:
        strsql += " LIMIT {0}".format(int(lnglimit))
    return _fetchall(strsql)


def f_select_networks(lnglimit=0):
    """Decision-#7 class: networks pad their real TMDb logo (~2.5k of 3.2k have one)."""
    # No POPULARITY column on networks; SERIE_COUNT is the usage proxy.
    strsql = (
        "SELECT ID_NETWORK AS id_item, NETWORK_NAME AS name, LOGO_PATH AS logo_path "
        "FROM " + strsqlns + "T2S_NETWORK "
        "WHERE LOGO_PATH IS NOT NULL AND LOGO_PATH<>'' AND (DELETED IS NULL OR DELETED=0) "
        "ORDER BY SERIE_COUNT DESC"
    )
    if lnglimit:
        strsql += " LIMIT {0}".format(int(lnglimit))
    return _fetchall(strsql)


def f_select_generic(stritemclass, lnglimit=0):
    """
    Generic placeholder for classes not yet wired (review §11 Phases 2-5). Returns [] with a
    notice so the driver runs without guessing column names. Implement per class as phases land.
    """
    print("  [select] class '{0}' not wired yet -- add its SELECT in f_select_generic. "
          "(Phase-1 implements technicals.)".format(stritemclass))
    return []


def _fetchall(strsql):
    conn = cp.f_getconnection()
    cur = conn.cursor()
    cur.execute(strsql)
    return cur.fetchall() or []


SELECTORS = {
    "technical": f_select_technicals,
    "company": f_select_companies,
    "network": f_select_networks,
}


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------
def f_run_single(args):
    strrep = f_choose_representation(args.item_class, args.instance_of, args.representation)
    if strrep == "logo-pad":
        # Deterministic padding path (decision #7): no model calls, zero cost. The logo path
        # is looked up from the class table by ID inside f_pad_logo_image.
        res = si.f_pad_logo_image(
            stritemclass=args.item_class, lngiditem=args.item_id, strname=args.name,
            intdryrun=1 if args.dry_run else 0, intforce=1 if args.force else 0,
        )
    else:
        res = si.f_generate_synthetic_image(
            stridwikidata=args.item_wikidata, stritemclass=args.item_class,
            lngiditem=args.item_id, strname=args.name, stroverview=args.overview,
            strrepresentation=strrep, lngseed=args.seed,
            intdryrun=1 if args.dry_run else 0, intforce=1 if args.force else 0,
        )
    _print_result(args.name or args.item_wikidata or args.item_id, res)
    return res


def f_run_batch(args):
    selector = SELECTORS.get(args.item_class)
    arritems = selector(args.limit) if selector else f_select_generic(args.item_class, args.limit)
    print("Selected {0} item(s) for class '{1}'.".format(len(arritems), args.item_class))

    dblspent = 0.0
    lngdone = lngskip = lngfail = 0
    for arr in arritems:
        if dblspent >= dblbudget:
            print("Budget ceiling ${0:.2f} reached -- stopping.".format(dblbudget))
            break
        strrep = f_choose_representation(
            args.item_class, str(arr.get("instance_of") or ""), args.representation
        )
        if strrep == "logo-pad":
            res = si.f_pad_logo_image(
                stritemclass=args.item_class, lngiditem=arr.get("id_item"),
                strname=str(arr.get("name") or ""),
                strlogopath=str(arr.get("logo_path") or ""),
                intdryrun=1 if args.dry_run else 0, intforce=1 if args.force else 0,
            )
        else:
            res = si.f_generate_synthetic_image(
                stridwikidata=arr.get("id_wikidata"), stritemclass=args.item_class,
                lngiditem=arr.get("id_item"), strname=str(arr.get("name") or ""),
                stroverview=str(arr.get("overview") or ""), strrepresentation=strrep,
                intdryrun=1 if args.dry_run else 0, intforce=1 if args.force else 0,
            )
        _print_result(arr.get("name"), res)
        dblspent += res.get("cost") or 0.0
        if res["status"] == "generated":
            lngdone += 1
        elif res["status"] == "skipped":
            lngskip += 1
        else:
            lngfail += 1
    print("\nDone. generated={0} skipped={1} failed={2} spent=${3:.3f}".format(
        lngdone, lngskip, lngfail, dblspent))


def _print_result(strlabel, res):
    print("  [{0}] {1} -- key={2} {3}".format(
        res["status"], strlabel, res.get("image_key", "")[:10],
        res.get("image_path") or "; ".join(res.get("messages", []))))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def f_parse_args(argv):
    p = argparse.ArgumentParser(description="Synthetic entity illustrations (Project A).")
    p.add_argument("--class", dest="item_class", help="entity class, e.g. technical, location, genre")
    p.add_argument("--item-wikidata", dest="item_wikidata", help="single-item: ID_WIKIDATA (Qxxxx)")
    p.add_argument("--item-id", dest="item_id", type=int, help="single-item: the class PK")
    p.add_argument("--name", default="", help="single-item: entity name (label for the description)")
    p.add_argument("--overview", default="", help="single-item: source text override")
    p.add_argument("--instance-of", dest="instance_of", default="", help="Wikidata INSTANCE_OF / sub-type")
    p.add_argument("--representation", default="", help="force a representation (else chosen by rule)")
    p.add_argument("--seed", type=int, default=None, help="text-to-image seed (determinism)")
    p.add_argument("--threshold", type=int, default=lngthresholddefault, help="usage threshold (batch)")
    p.add_argument("--limit", type=int, default=0, help="max items this run (batch)")
    p.add_argument("--dry-run", action="store_true", help="no API calls, no DB writes")
    p.add_argument("--force", action="store_true", help="regenerate even if the image exists")
    return p.parse_args(argv)


def main(argv):
    args = f_parse_args(argv)
    if not args.item_class:
        print("Specify --class. Single item: add --item-wikidata/--item-id. Batch: just --class.")
        return 2
    if args.item_wikidata or args.item_id:
        f_run_single(args)
    else:
        f_run_batch(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
