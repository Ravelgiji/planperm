import { COOKIE_NAME } from "@shared/const";
import { z } from "zod";
import { savePropertyDocument, saveSiteContextNote } from "./db";
import { getSessionCookieOptions } from "./_core/cookies";
import { systemRouter } from "./_core/systemRouter";
import { protectedProcedure, publicProcedure, router } from "./_core/trpc";
import { fetchNearbyPlanningApplications } from "./planning";
import { storagePut } from "./storage";

const siteInput = z.object({
  siteLabel: z.string().trim().min(1).max(255),
  lat: z.number().finite().min(-90).max(90),
  lng: z.number().finite().min(-180).max(180),
});

export const appRouter = router({
    // if you need to use socket.io, read and register route in server/_core/index.ts, all api should start with '/api/' so that the gateway can route correctly
  system: systemRouter,
  auth: router({
    me: publicProcedure.query(opts => opts.ctx.user),
    logout: publicProcedure.mutation(({ ctx }) => {
      const cookieOptions = getSessionCookieOptions(ctx.req);
      ctx.res.clearCookie(COOKIE_NAME, { ...cookieOptions, maxAge: -1 });
      return {
        success: true,
      } as const;
    }),
  }),
  planning: router({
    nearby: publicProcedure.input(siteInput.extend({ radiusKm: z.number().min(0.25).max(5) })).query(({ input }) => fetchNearbyPlanningApplications(input)),
  }),
  propertyContext: router({
    upload: protectedProcedure.input(siteInput.extend({
      fileName: z.string().trim().min(1).max(255),
      mimeType: z.string().trim().min(1).max(120),
      byteLength: z.number().int().positive().max(10 * 1024 * 1024),
      dataBase64: z.string().min(1).max(14 * 1024 * 1024),
      contextNote: z.string().trim().max(2000).optional(),
    })).mutation(async ({ ctx, input }) => {
      const safeName = input.fileName.replace(/[^a-zA-Z0-9._-]/g, "_");
      const buffer = Buffer.from(input.dataBase64, "base64");
      if (buffer.byteLength !== input.byteLength) throw new Error("The uploaded file size could not be verified.");
      const stored = await storagePut(`planperm/${ctx.user.id}/property-context/${Date.now()}-${safeName}`, buffer, input.mimeType);
      const persisted = await savePropertyDocument({
        userId: ctx.user.id,
        siteLabel: input.siteLabel,
        latitude: input.lat.toFixed(7),
        longitude: input.lng.toFixed(7),
        fileName: input.fileName,
        mimeType: input.mimeType,
        fileSize: input.byteLength,
        storageKey: stored.key,
        storageUrl: stored.url,
        contextNote: input.contextNote || null,
      });
      return { ...stored, persisted, fileName: input.fileName, contextNote: input.contextNote || null };
    }),
    recordNote: protectedProcedure.input(siteInput.extend({ content: z.string().trim().min(1).max(2000) })).mutation(async ({ ctx, input }) => ({
      persisted: await saveSiteContextNote({ userId: ctx.user.id, siteLabel: input.siteLabel, latitude: input.lat.toFixed(7), longitude: input.lng.toFixed(7), content: input.content }),
    })),
  }),
});

export type AppRouter = typeof appRouter;
