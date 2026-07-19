import { ToolEditor } from "@/components/tool-builder/ToolEditor";
import {
  listCredentials,
  listCustomPythonCatalog,
  listToolkitCatalog,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function NewToolPage() {
  const token = await getServerAgentOsToken();
  const [credentials, toolkitCatalog, customPythonCatalog] = await Promise.all([
    listCredentials(token),
    listToolkitCatalog(token),
    listCustomPythonCatalog(token),
  ]);
  return (
    <ToolEditor
      credentials={credentials}
      toolkitCatalog={toolkitCatalog}
      customPythonCatalog={customPythonCatalog}
    />
  );
}
