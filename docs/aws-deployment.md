# AWS deployment target

Recommended first production shape:

| Concern | Service |
| --- | --- |
| Backend / AgentOS | ECS Fargate service behind ALB |
| Frontend | ECS Fargate or Amplify/CloudFront |
| Database | RDS PostgreSQL with pgvector |
| Documents | S3 + private signed URLs |
| Secrets | Secrets Manager + KMS envelope keys |
| Auth | Clerk (external) verified via JWKS |
| Quotas | API Gateway / WAF + Redis (ElastiCache) |

Keep `AUTH_DISABLED=false` in every non-local environment. Rotate `CREDENTIAL_ENCRYPTION_KEY` / KMS CMK on a schedule and rehearse restore from RDS snapshots.
