"""One-way bindings to the approved private export; contains no financial values."""
WORKBOOK_SHA256 = '6651384ebe7d0ae0b468c4f47e667ad1309855f43c6cb6b7bf8ef9ac61057369'
PLAN_DIGEST = 'cf216e827d251c47247f8181175d86c21b2e5591648ea9987e7bbe339918d211'
SCHEMA_DIGEST = '3d6f1a21fbb41a211f5097af7f5defcd367de240c88183941fe977dc5083467e'
EXTENDED_CATALOG_DIGEST = '6cae0b306f98590d14e06e6f889752398352c43aee667b656d6140b738520e3a'
# Capture provenance only; release authorization remains the two server-side SHAs.
EXTENDED_CATALOG_REVIEW = {
    'evidence_file_sha256': '894a14233d2b66c8eb176c2ad4655437024cf9b0f1b9b7ec67c207592ced218f',
    'evidence_digest': 'df3e48dc0b4f7aafd8a23224a59e38b12fd25ffd517b0255266fda9efdbcd6de',
    'captured_at': '2026-09-30T06:35:16.364313+00:00',
    'capture_release_sha': '0e141137d656b1182cbcc8760aa92e7be0b26b2c',
    'pdf_sha256': 'b8413ee856c8bbc14529662297e70f7057ea9b992335c8269c5b43a8f5bf4d0f',
    'preconditions_digest': 'fcf936480d89243c0ffaa7ef5e9582c95d82f616b84340ffa043ef64063831c5',
    'catalog_digest': EXTENDED_CATALOG_DIGEST,
    'catalog_counts': {'columns': 90, 'constraints': 14, 'dependencies': 116,
                       'indexes': 32, 'relations': 8, 'triggers': 0},
}
HASH_BINDINGS = {
('classified_transactions','5910'): '48e7e57a38258abd6eac333c037f45e22c23fa2ae6fa62eae3d675eed06f50d3',
('classified_transactions','5911'): 'e36d0b858fa7689909108deec65ab1e8d5cf10060338a6b45527506b350781f8',
('classified_transactions','5912'): 'f5a289984c3244f3f336fbd2eb7610d2308dad357778638df690afaa69297b1e',
('classified_transactions','5913'): 'e802333e5cfe926c3c9316d8ff57e79774a0ebaf848496b553548aedda9f80e8',
('classified_transactions','5914'): '7e799aa26c19431169881ece23f56c6919bd7d4e14871c1a5ee3ef6893137f29',
('classified_transactions','5915'): '204f667e5eaf0090f80e69d0ee938f091ebd277dc78dd3907c80944e107ccdc5',
('classified_transactions','5916'): '40f9cd777f2edc10bca283f7b6abaee3d8f4e0f945b9ea51e3af53f77f28d596',
('classified_transactions','5917'): '7a82ab4fbe71fed34ebece29a7b1b5d41adbdbb1c0667386908107b8dae5b355',
('classified_transactions','5918'): '53f9c627777fa7710a5138d979f1cccd139ca80ba31fdb139279fb69793daa7c',
('statement_balances','165'): '6536746120127c4fc058273e179bc8da2a669a5d6c20fda46d28299c4d345624',
('statement_balances','167'): '5b315d48d0c3cd7ad83283ccfa602222f0dee9d0d9958227c58c22848de80ea8',
('statement_balances','168'): '56a8f8919dd85c898fec987f3644e43b9fde3ea84cd190468aa869c8d68a219f',
('statement_balances','170'): 'b0923bcb5ab3a8e95dc771969c96543aa27aee697ad2c2fc997e352d89395c1d',
('statement_imports','169'): '1198de23eb40fe01a7fc47c319ddc3a0cec2d698a2d48dc3e1a5b7de7f4c465d',
('statement_imports','170'): '4d9f48bf6dcb405c50d088577e6fd9722ce5eb7c1421187b97e17cab88a1a4b1',
('statement_imports','171'): '9012301b38281aff91ca9a1b90c458aef61f7d72eb5a542197545467670b84ec',
('statement_imports','172'): '1ea44e45e9e05d3cc72c4e12f8cd8dc3d390d3e46a6952fb30b3383ff04df8e8',
('statement_imports','173'): '4b6e98e9e13f72fe878c7744a30e60968969ba2cbb8abc093178815f537698b2',
('statement_imports','174'): 'bfb41696af2b98380af5b920c7bca7b26b4f1ec5d6140203ffa86d25dca46f9e',
('category_list','71966'): '38aa5fbbe4d670d9408253f3ca0e26194ed3885937bc671f293a0f0ca86f4b74',
('category_list','71964'): '6ae93f99fbdea870465c111799ea1ee9a75baf4b7abcea1e12fd8ac7ac9f3e2c',
('category_list','71965'): '808df58dfc8cf522e43d0a0a3f0ee887f0db4d8b5649a5adc933bf5755ec6a77',
('category_list','71949'): 'a8a6ebb124814d047a08d84a7dee594fdf55b84b33c4895916d3ea2ab7726a86',
('complete import scope + schema + dependencies','ALL'): '0417205bbce70a27718e8092a26f7ec8998ec5e81ec2d2e1a8b79a196e8d238b',
}
