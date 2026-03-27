# Knowledge API Reference

## Authentication

Clients authenticate with bearer tokens issued by the corporate identity provider. Tokens expire after one hour; refresh using the standard OAuth2 refresh grant.

## Query endpoint

POST `/api/v1/chat/query` accepts a JSON body with `question`, optional `conversation_id`, and optional `collection_ids`. Responses include `answer`, `citations`, and `confidence`.

## Rate limits

Default quota is six hundred requests per minute per application credential. Throttling returns HTTP 429 with a `Retry-After` header.
