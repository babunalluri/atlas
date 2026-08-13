import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
  interface User {
    accessToken?: string;
    refreshToken?: string;
    idToken?: string;
    expiresAt?: number;
    orgId?: string;
    orgRole?: string;
  }

  interface Session {
    accessToken?: string;
    idToken?: string;
    orgId?: string;
    error?: string;
    endSessionUrl?: string;
    user: {
      id?: string;
      name?: string | null;
      email?: string | null;
      image?: string | null;
    };
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    idToken?: string;
    expiresAt?: number;
    orgId?: string;
    orgRole?: string;
    error?: string;
  }
}
