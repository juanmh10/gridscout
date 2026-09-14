import os
import asyncio
from packages.ai.model_gateway import VertexModelGateway
from packages.retrieval.embeddings import GeminiEmbeddingProvider

# Load .env
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

async def main():
    print("==================================================")
    print("      Testing Live Vertex AI / Gemini API Connection")
    print("==================================================")

    api_key = os.getenv("GEMINI_API_KEY")
    project_id = os.getenv("VERTEX_PROJECT_ID")
    location = os.getenv("VERTEX_LOCATION", "global")
    model_extraction = os.getenv("VERTEX_MODEL_EXTRACTION", "gemini-2.5-flash")
    model_investigation = os.getenv("VERTEX_MODEL_INVESTIGATION", model_extraction)
    
    print(f"Config: project_id={'[CONFIGURED]' if project_id else 'None'}, location={location}, api_key={'[CONFIGURED]' if api_key else 'None'}")
    
    # 1. Test Model Gateway Normalization
    print("\n--- 1. Testing Live Model Normalization ---")
    gw = VertexModelGateway(
        project_id=project_id,
        api_key=api_key,
        location=location,
        model_extraction=model_extraction,
        model_investigation=model_investigation
    )
    
    test_title = "Placa de Video Galax GeForce RTX 3080 SG 1-Click OC 10GB GDDR6X"
    test_desc = "Usada apenas para jogos, com nota fiscal de compra e caixa original completa. Sem defeitos, pasta termica trocada recentemente."
    
    print(f"Models: extraction={model_extraction}, investigation={model_investigation}")
    print(f"Input Title: {test_title}")
    try:
        norm_result = await gw.normalize_listing(
            title=test_title,
            description=test_desc,
            category_hint="gpu"
        )
        print(f"Normalization Success!")
        print(f"  Category:   {norm_result.category}")
        print(f"  Brand:      {norm_result.brand}")
        print(f"  Model:      {norm_result.model}")
        print(f"  Variant:    {norm_result.variant}")
        print(f"  Confidence: {norm_result.confidence}")
        print(f"  Attributes: {norm_result.extracted_attributes}")
    except Exception as e:
        print(f"Normalization Failed: {e}")

    # 2. Test Live Embeddings
    print("\n--- 2. Testing Live Gemini Embeddings ---")
    ep = GeminiEmbeddingProvider(
        project_id=project_id,
        api_key=api_key,
        location=location,
        model="text-embedding-004",
        dim=768
    )
    
    test_text = "NVIDIA RTX 3080 thermal pad inspection and VRAM temperatures"
    try:
        emb_vector = ep.embed_text(test_text)
        print(f"Embedding Success!")
        print(f"  Vector Dimensions: {len(emb_vector)}")
        print(f"  Vector Sample (first 5 elements): {emb_vector[:5]}")
        assert len(emb_vector) == 768
    except Exception as e:
        print(f"Embedding Failed: {e}")

    print("\n==================================================")
    print("      Live Vertex / Gemini Validation Finished")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
