import { decimal, int, mysqlEnum, mysqlTable, text, timestamp, varchar } from "drizzle-orm/mysql-core";

/** Core authenticated user record. */
export const users = mysqlTable("users", {
  id: int("id").autoincrement().primaryKey(),
  openId: varchar("openId", { length: 64 }).notNull().unique(),
  name: text("name"),
  email: varchar("email", { length: 320 }),
  loginMethod: varchar("loginMethod", { length: 64 }),
  role: mysqlEnum("role", ["user", "admin"]).default("user").notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
  lastSignedIn: timestamp("lastSignedIn").defaultNow().notNull(),
});

/** Stored file metadata; binary data remains in S3 rather than the database. */
export const propertyDocuments = mysqlTable("propertyDocuments", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  siteLabel: varchar("siteLabel", { length: 255 }).notNull(),
  latitude: decimal("latitude", { precision: 10, scale: 7 }).notNull(),
  longitude: decimal("longitude", { precision: 10, scale: 7 }).notNull(),
  fileName: varchar("fileName", { length: 255 }).notNull(),
  mimeType: varchar("mimeType", { length: 120 }).notNull(),
  fileSize: int("fileSize").notNull(),
  storageKey: varchar("storageKey", { length: 500 }).notNull(),
  storageUrl: varchar("storageUrl", { length: 700 }).notNull(),
  contextNote: text("contextNote"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
});

/** A concise planning note captured alongside a selected property site. */
export const siteContextNotes = mysqlTable("siteContextNotes", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  siteLabel: varchar("siteLabel", { length: 255 }).notNull(),
  latitude: decimal("latitude", { precision: 10, scale: 7 }).notNull(),
  longitude: decimal("longitude", { precision: 10, scale: 7 }).notNull(),
  content: text("content").notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
});

export type User = typeof users.$inferSelect;
export type InsertUser = typeof users.$inferInsert;
export type InsertPropertyDocument = typeof propertyDocuments.$inferInsert;
export type InsertSiteContextNote = typeof siteContextNotes.$inferInsert;
