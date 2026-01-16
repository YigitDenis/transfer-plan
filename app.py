import streamlit as st
import pandas as pd
from io import BytesIO
import re

# -------------------------
# PAGE CONFIG
# -------------------------
st.set_page_config(page_title="Transfer Planı", layout="wide")

# -------------------------
# HACKER THEME (CSS)
# -------------------------
def apply_hacker_theme():
    st.markdown(
        """
        <style>
        .stApp { background: #070A0F; color: #D7FFE7; }
        h1, h2, h3, h4 { color: #39FF14 !important; letter-spacing: .5px; }
        [data-testid="stMetricValue"] { color: #39FF14 !important; }
        [data-testid="stMetricLabel"] { color: #B8FFC0 !important; }
        .stButton>button {
            background: #0F1A12; border: 1px solid #39FF14; color: #39FF14;
            border-radius: 12px; padding: 10px 14px;
        }
        .stButton>button:hover { background: #122417; }
        [data-testid="stDataFrame"] { border: 1px solid #1C3A26; border-radius: 12px; }
        .stDownloadButton>button {
            background: #0F1A12; border: 1px solid #39FF14; color: #39FF14;
            border-radius: 12px; padding: 10px 14px;
        }
        .stDownloadButton>button:hover { background: #122417; }
        </style>
        """,
        unsafe_allow_html=True
    )

apply_hacker_theme()

# -------------------------
# HEADER
# -------------------------
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

# -------------------------
# EXCEL TEMPLATE (DOWNLOAD)
# -------------------------
def make_template_excel_bytes() -> bytes:
    template = pd.DataFrame([
        {
            "MağazaAdı": "İzmir Alsancak Mağaza",
            "ÜrünKodu": "SKU123",
            "ÜrünAdı": "Örnek Ürün",
            "RenkKodu": "001",
            "RenkAçıklaması": "Siyah",
            "Beden": "M",
            "NetMiktar": 5,
            "Envanter": 2,
        }
    ])

    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        template.to_excel(writer, index=False, sheet_name="TEMPLATE")
    return out.getvalue()

with st.container():
    st.subheader("🧩 Excel Şablonu")
    c1, c2 = st.columns([1, 2])
    with c1:
        st.download_button(
            label="⬇️ Şablonu indir (Excel)",
            data=make_template_excel_bytes(),
            file_name="transfer_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with c2:
        st.caption("Şablonu indir → doldur → aşağıdan yükle. (Kolon isimlerini değiştirmeyin)")

st.divider()

# -------------------------
# HELPERS
# -------------------------
def norm_col(name: str) -> str:
    """Tüm whitespace karakterlerini (boşluk, \\n, \\t vs.) siler."""
    name = str(name)
    name = re.sub(r"\s+", "", name, flags=re.UNICODE)
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
    cadde_received_per_sku = {}

    group_cols = ["Ürün kodu", "Ürün adı", "Renk kodu", "Renk adı", "Beden"]

    for _, g in df.groupby(group_cols, dropna=False):
        g = g.copy()

        internet_rows = g[g["Mağaza"] == INTERNET_STORE_NAME]
        if not internet_rows.empty and int(internet_rows.iloc[0]["Stok"]) >= 10:
            continue

        g["eligible_receive"] = (g["Satış"] > g["Stok"]) & (g["Stok"] < 4)

        g["need"] = 0
        stock0_mask = (g["Stok"] == 0) & (g["Satış"] > 0) & g["eligible_receive"]
        g.loc[stock0_mask, "need"] = g.loc[stock0_mask, "Satış"] * 2

        normal_mask = (~stock0_mask) & g["eligible_receive"]
        g.loc[normal_mask, "need"] = (g.loc[normal_mask, "Satış"] - g.loc[normal_mask, "Stok"]).clip(lower=0)

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

        # ✅ Internet Mağaza ASLA gönderen olamaz
        suppliers = g[(g["sendable"] > 0) & (g["Mağaza"] != INTERNET_STORE_NAME)].copy()
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
uploaded_file = st.file_uploader("📥 Rapor dosyasını yükle (Excel)", type=["xlsx"])

if uploaded_file:
    df_raw = pd.read_excel(uploaded_file)
    df_raw = normalize_columns(df_raw)

    missing = [c for c in REQUIRED_NORM_COLS if c not in df_raw.columns]
    if missing:
        st.error(f"Eksik kolonlar var: {missing}")
        st.write("Dosyadaki kolonlar:", list(df_raw.columns))
        st.stop()

    df = to_internal_schema(df_raw)

    st.success("Dosya formatı doğru. (Kolonlar normalize edildi)")
    st.subheader("🧾 Yüklenen Veri")
    st.dataframe(df, use_container_width=True)

    if st.button("🚀 Başlat"):
        st.info("Kurallar çalıştırılıyor...")

        output = build_transfers(df, max_per_row=4)

        st.session_state["input_df"] = df
        st.session_state["transfer_df"] = output

        st.subheader("📦 Transfer Çıktısı")
        st.dataframe(output, use_container_width=True)

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            output.to_excel(writer, index=False, sheet_name="transfer")
        buffer.seek(0)

        st.download_button(
            label="⬇️ Çıktıyı Excel olarak indir",
            data=buffer,
            file_name="transfer_cikti.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        st.divider()

        # -------------------------
        # SUMMARY AREA
        # -------------------------
        st.subheader("📊 Özet Panel")

        input_df = st.session_state.get("input_df")
        transfer_df = st.session_state.get("transfer_df")

        total_net_sales = int(input_df["Satış"].sum()) if input_df is not None and not input_df.empty else 0
        total_inventory = int(input_df["Stok"].sum()) if input_df is not None and not input_df.empty else 0

        if transfer_df is not None and not transfer_df.empty and "Gönderilen adet" in transfer_df.columns:
            transfer_df["Gönderilen adet"] = pd.to_numeric(
                transfer_df["Gönderilen adet"], errors="coerce"
            ).fillna(0).astype(int)
            total_ship_units = int(transfer_df["Gönderilen adet"].sum())
        else:
            total_ship_units = 0

        m1, m2, m3 = st.columns(3)
        m1.metric("Net Satış (Toplam)", f"{total_net_sales}")
        m2.metric("Envanter / Stok (Toplam)", f"{total_inventory}")
        m3.metric("Gönderilecek Ürün Adedi (Toplam)", f"{total_ship_units}")

        # ✅ En çok transfer yapılan Ürün Kodu + Ürün Adı + Renk (+ adet)
        st.markdown("### 🏆 En Çok Transfer Yapılan Ürün (Kod + Ürün + Renk)")
        if transfer_df is None or transfer_df.empty:
            st.info("Transfer yok, en çok transfer yapılan ürün bulunamadı.")
        else:
            top_product = (
                transfer_df.groupby(["Ürün kodu", "Ürün adı", "Renk adı"], as_index=False)["Gönderilen adet"]
                .sum()
                .sort_values("Gönderilen adet", ascending=False)
                .head(1)
            )
            if top_product.empty:
                st.info("Transfer yok, en çok transfer yapılan ürün bulunamadı.")
            else:
                tp = top_product.iloc[0]
                st.dataframe(
                    pd.DataFrame([{
                        "Ürün kodu": tp["Ürün kodu"],
                        "Ürün adı": tp["Ürün adı"],
                        "Renk adı": tp["Renk adı"],
                        "Transfer adedi": int(tp["Gönderilen adet"])
                    }]),
                    use_container_width=True
                )

        st.markdown("### 🔽 Net Satış / Envanter / Gönderilecek Adet (Toplam)")
        totals_df = pd.DataFrame([{
            "Net Satış": total_net_sales,
            "Envanter": total_inventory,
            "Gönderilecek Ürün Adedi": total_ship_units
        }])
        st.dataframe(totals_df, use_container_width=True)

        st.markdown("### ✅ Alan Mağaza Özeti")
        if transfer_df is None or transfer_df.empty:
            st.info("Transfer çıktısı boş. (Özet tablo oluşmadı)")
        else:
            recv_summary = (
                transfer_df.groupby("Alan mağaza", as_index=False)["Gönderilen adet"]
                .sum()
                .rename(columns={"Gönderilen adet": "Alınan adet"})
                .sort_values("Alınan adet", ascending=False)
            )
            st.dataframe(recv_summary, use_container_width=True)

            st.markdown("### 🚚 Gönderen Mağaza Özeti")
            send_summary = (
                transfer_df.groupby("Gönderen mağaza", as_index=False)["Gönderilen adet"]
                .sum()
                .rename(columns={"Gönderilen adet": "Gönderilen adet"})
                .sort_values("Gönderilen adet", ascending=False)
            )
            st.dataframe(send_summary, use_container_width=True)

            st.markdown("### 🔁 Gönderen → Alan Kırılımı")
            pivot = pd.pivot_table(
                transfer_df,
                index="Gönderen mağaza",
                columns="Alan mağaza",
                values="Gönderilen adet",
                aggfunc="sum",
                fill_value=0
            )
            st.dataframe(pivot, use_container_width=True)

else:
    st.caption("Excel'i yükleyin, sonra Başlat'a basın.")
