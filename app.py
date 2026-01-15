import streamlit as st
import pandas as pd
from io import BytesIO
import re

st.set_page_config(page_title="Transfer Planı", layout="wide")
st.title("Mağazalar Arası Transfer Planı")

INTERNET_STORE_NAME = "Internet Mağaza"
CADDE_STORE_NAME = "Caddebostan Mağaza"

# ✅ Beklenen kolonlar (normalize edilmiş halleri: boşluk/satır atlaması yok)
REQUIRED_NORM_COLS = [
    "MağazaAdı",
    "ÜrünKodu",
    "ÜrünAdı",
    "RenkKodu",
    "RenkAçıklaması",
    "Beden",
    "NetMiktar",
    "Envanter",
]

OUTPUT_COLS = [
    "Gönderen mağaza",
    "Ürün kodu",
    "Ürün adı",
    "Renk kodu",
    "Renk adı",
    "Beden",
    "Gönderilen adet",
    "Alan mağaza",
]

def norm_col(name: str) -> str:
    """
    Kolon adlarını normalize eder:
    - \n, \t, boşluk, NBSP vb. tüm whitespace karakterlerini siler
    """
    name = str(name)
    name = re.sub(r"\s+", "", name, flags=re.UNICODE)  # tüm whitespace -> sil
    return name

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [norm_col(c) for c in df.columns]
    return df

def normalize_numeric(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").fillna(0)
    s = s.clip(lower=0)
    return s.round(0).astype(int)

def to_internal_schema(df_raw: pd.DataFrame) -> pd.DataFrame:
    # df_raw kolonları zaten normalize edildi varsayımı
    rename_map = {
        "MağazaAdı": "Mağaza",
        "ÜrünKodu": "Ürün kodu",
        "ÜrünAdı": "Ürün adı",
        "RenkKodu": "Renk kodu",
        "RenkAçıklaması": "Renk adı",
        "NetMiktar": "Satış",
        "Envanter": "Stok",
        "Beden": "Beden",
    }
    df = df_raw.rename(columns=rename_map).copy()

    internal_cols = ["Mağaza", "Ürün kodu", "Ürün adı", "Renk kodu", "Renk adı", "Beden", "Satış", "Stok"]
    df = df[[c for c in internal_cols if c in df.columns]]

    df["Satış"] = normalize_numeric(df["Satış"])
    df["Stok"] = normalize_numeric(df["Stok"])

    for c in ["Mağaza", "Ürün kodu", "Ürün adı", "Renk kodu", "Renk adı", "Beden"]:
        df[c] = df[c].astype(str).str.strip()

    return df

def build_transfers(df: pd.DataFrame, max_per_row: int = 4) -> pd.DataFrame:
    out_rows = []
    cadde_received_per_sku = {}  # Cadde'ye SKU bazlı gelen toplam

    group_cols = ["Ürün kodu", "Ürün adı", "Renk kodu", "Renk adı", "Beden"]

    for _, g in df.groupby(group_cols, dropna=False):
        g = g.copy()

        # Internet Mağaza: beden bazlı stok >=10 ise bu ürün+beden için transfer yok
        internet_rows = g[g["Mağaza"] == INTERNET_STORE_NAME]
        if not internet_rows.empty and int(internet_rows.iloc[0]["Stok"]) >= 10:
            continue

        # Alıcı: Satış>Stok ve Stok<4
        g["eligible_receive"] = (g["Satış"] > g["Stok"]) & (g["Stok"] < 4)

        # Need (alım hakkı)
        g["need"] = 0
        stock0_mask = (g["Stok"] == 0) & (g["Satış"] > 0) & g["eligible_receive"]
        g.loc[stock0_mask, "need"] = g.loc[stock0_mask, "Satış"] * 2

        normal_mask = (~stock0_mask) & g["eligible_receive"]
        g.loc[normal_mask, "need"] = (g.loc[normal_mask, "Satış"] - g.loc[normal_mask, "Stok"]).clip(lower=0)

        # Gönderici: Satış=0 veya Stok>Satış
        g["sendable"] = 0
        sales0 = g["Satış"] == 0
        g.loc[sales0, "sendable"] = g.loc[sales0, "Stok"]
        g.loc[~sales0, "sendable"] = (g.loc[~sales0, "Stok"] - g.loc[~sales0, "Satış"]).clip(lower=0)

        g["eligible_send"] = (g["Satış"] == 0) | (g["Stok"] > g["Satış"])
        g.loc[~g["eligible_send"], "sendable"] = 0

        receivers = g[g["need"] > 0].copy()
        if receivers.empty:
            continue

        receivers["prio_stock0"] = ((receivers["Stok"] == 0) & (receivers["Satış"] > 0)).astype(int)
        receivers = receivers.sort_values(
            by=["prio_stock0", "need", "Satış"],
            ascending=[False, False, False],
            kind="mergesort",
        )

        suppliers = g[g["sendable"] > 0].copy()
        if suppliers.empty:
            continue

        suppliers["prio_sales0"] = (suppliers["Satış"] == 0).astype(int)
        suppliers = suppliers.sort_values(
            by=["prio_sales0", "Satış", "sendable", "Stok"],
            ascending=[False, True, False, False],
            kind="mergesort",
        )

        sup_sendable = {row["Mağaza"]: int(row["sendable"]) for _, row in suppliers.iterrows()}

        for _, r in receivers.iterrows():
            recv_store = r["Mağaza"]
            remaining_need = int(r["need"])
            if remaining_need <= 0:
                continue

            for _, s in suppliers.iterrows():
                send_store = s["Mağaza"]
                if send_store == recv_store:
                    continue

                available = sup_sendable.get(send_store, 0)
                if available <= 0:
                    continue

                qty = min(remaining_need, available, max_per_row)

                # Cadde mağaza: SKU bazında max 3 adet alır
                if recv_store == CADDE_STORE_NAME:
                    sku = r["Ürün kodu"]
                    used = cadde_received_per_sku.get(sku, 0)
                    cap = max(0, 3 - used)
                    qty = min(qty, cap)
                    if qty <= 0:
                        continue

                if qty <= 0:
                    continue

                out_rows.append({
                    "Gönderen mağaza": send_store,
                    "Ürün kodu": r["Ürün kodu"],
                    "Ürün adı": r["Ürün adı"],
                    "Renk kodu": r["Renk kodu"],
                    "Renk adı": r["Renk adı"],
                    "Beden": r["Beden"],
                    "Gönderilen adet": int(qty),
                    "Alan mağaza": recv_store,
                })

                sup_sendable[send_store] = available - qty
                remaining_need -= qty

                if recv_store == CADDE_STORE_NAME:
                    cadde_received_per_sku[r["Ürün kodu"]] = cadde_received_per_sku.get(r["Ürün kodu"], 0) + qty

                if remaining_need <= 0:
                    break

    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLS)

    return pd.DataFrame(out_rows, columns=OUTPUT_COLS)


# ---------------- UI ----------------
uploaded_file = st.file_uploader("Rapor dosyasını yükle (Excel)", type=["xlsx"])

if uploaded_file:
    df_raw = pd.read_excel(uploaded_file)

    # ✅ Kolonları normalize et (satır atlaması/boşluklar gider)
    df_raw = normalize_columns(df_raw)

    missing = [c for c in REQUIRED_NORM_COLS if c not in df_raw.columns]
    if missing:
        st.error(f"Eksik kolonlar var: {missing}")
        st.write("Dosyadaki kolonlar:", list(df_raw.columns))
        st.stop()

    df = to_internal_schema(df_raw)

    st.success("Dosya formatı doğru. (Kolonlar normalize edildi)")
    st.subheader("Yüklenen Veri")
    st.dataframe(df, use_container_width=True)

    if st.button("Başlat"):
        st.info("Kurallar çalıştırılıyor...")

        output = build_transfers(df, max_per_row=4)

        st.subheader("Transfer Çıktısı")
        st.dataframe(output, use_container_width=True)

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            output.to_excel(writer, index=False, sheet_name="transfer")
        buffer.seek(0)

        st.download_button(
            label="Çıktıyı Excel olarak indir",
            data=buffer,
            file_name="transfer_cikti.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
else:
    st.caption("Excel'i yükleyin, sonra Başlat'a basın.")
