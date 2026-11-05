from httpx import AsyncClient


async def test_nan_and_infinity_in_body_return_422(client: AsyncClient) -> None:
    # A public endpoint: anyone could send this. Python's JSON parser
    # accepts NaN/Infinity, the wrong values come back as text.
    response = await client.post(
        "/accounts/register",
        content='{"email": NaN, "password": Infinity}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    inputs = {error["loc"][-1]: error["input"] for error in response.json()["detail"]}
    assert inputs == {"email": "nan", "password": "inf"}
