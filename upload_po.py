from huggingface_hub import HfApi

api = HfApi()

api.create_repo(
    repo_id="FabioRS/BibliothecaPatristica",
    repo_type="dataset",
    private=False,
)

api.upload_file(
    path_or_fileobj="BibliothecaPatristica_PO.parquet",
    path_in_repo="data/PO.parquet",
    repo_id="FabioRS/BibliothecaPatristica",
    repo_type="dataset",
)
