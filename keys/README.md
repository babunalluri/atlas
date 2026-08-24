# Keys (local only)

Store OCI SSH private keys and similar secrets here.

- Files in this directory are **gitignored** — do not force-add them.
- Example: `keys/atlas-oci.key` then  
  `chmod 600 keys/atlas-oci.key`  
  `ssh -i keys/atlas-oci.key opc@<PUBLIC_IP>`
