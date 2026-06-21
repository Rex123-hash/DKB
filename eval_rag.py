# -*- coding: utf-8 -*-
"""Honest retrieval evaluation for the Dukanbook RAG pipeline.

For each test question we know which knowledge file SHOULD answer it. We run the
real retrieval and check whether a correct-topic file appears in the top results.
Reports Hit@1, Hit@3, Hit@5 and MRR (mean reciprocal rank).
"""
from app import db, rag

# (question, set of acceptable source files for that topic)
TESTS = [
    ("GST registration kab zaroori hoti hai?", {"gst_basics.md", "faq_gst.md"}),
    ("GST ke kitne tax slab hote hain?", {"gst_advanced.md", "faq_gst.md", "gst_basics.md"}),
    ("e-way bill kab banana padta hai?", {"gst_advanced.md", "faq_gst.md"}),
    ("composition scheme kya hai?", {"gst_basics.md", "gst_advanced.md", "faq_gst.md", "glossary.md"}),
    ("income tax return file karne ka fayda?", {"income_tax_basics.md", "faq_income_tax.md"}),
    ("44AD presumptive scheme kya hai?", {"income_tax_basics.md", "income_tax_advanced.md", "faq_income_tax.md"}),
    ("TDS kya hota hai?", {"income_tax_advanced.md", "faq_income_tax.md"}),
    ("udhaar kaise vasoolein customers se?", {"credit_udhaar_management.md", "business_tips.md", "faq_shop_operations.md"}),
    ("customer ko kitna credit limit dena chahiye?", {"credit_udhaar_management.md", "business_tips.md"}),
    ("MUDRA loan ke kitne type hain?", {"loans_and_schemes.md", "faq_loans_schemes.md"}),
    ("PM SVANidhi yojana kya hai?", {"loans_and_schemes.md", "faq_loans_schemes.md"}),
    ("CIBIL score kitna hona chahiye?", {"loans_and_schemes.md", "faq_loans_schemes.md", "glossary.md"}),
    ("FSSAI license kis ko chahiye?", {"licenses_compliance.md", "faq_licenses.md"}),
    ("Gumasta license kya hota hai?", {"licenses_compliance.md", "faq_licenses.md"}),
    ("stock turnover ka matlab kya hai?", {"inventory_management.md", "faq_shop_operations.md", "glossary.md"}),
    ("dead stock ka kya karein?", {"inventory_management.md", "faq_shop_operations.md"}),
    ("UPI QR code se kya fayda hota hai?", {"payments_banking.md", "faq_payments.md"}),
    ("ONDC par online kaise bechein?", {"online_selling.md"}),
    ("Diwali ke liye stock kaise plan karein?", {"festival_calendar.md", "inventory_management.md", "business_tips.md"}),
    ("proprietorship aur partnership me farak?", {"business_structure.md"}),
]


def main():
    conn = db.get_connection()
    n = len(TESTS)
    h1 = h3 = h5 = 0
    mrr = 0.0
    print(f"Knowledge base: {rag.count(conn)} chunks\n")
    print(f"{'Q#':<3}{'Hit?':<6}{'Rank':<6}{'Top file':<30}Question")
    print("-" * 92)
    for i, (q, exp) in enumerate(TESTS, 1):
        res = rag.search(conn, q, k=5)
        srcs = [r["source"] for r in res]
        rank = next((j + 1 for j, s in enumerate(srcs) if s in exp), None)
        if rank:
            mrr += 1.0 / rank
            h1 += rank <= 1
            h3 += rank <= 3
            h5 += rank <= 5
        mark = "yes" if rank else "MISS"
        print(f"{i:<3}{mark:<6}{str(rank or '-'):<6}{srcs[0]:<30}{q[:34]}")
    print("-" * 92)
    print(f"\nQuestions tested : {n}")
    print(f"Hit@1            : {h1}/{n} = {h1/n:.0%}")
    print(f"Hit@3            : {h3}/{n} = {h3/n:.0%}")
    print(f"Hit@5            : {h5}/{n} = {h5/n:.0%}")
    print(f"MRR              : {mrr/n:.3f}")


if __name__ == "__main__":
    main()