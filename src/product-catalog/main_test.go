// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"reflect"
	"strings"
	"testing"

	pb "github.com/opentelemetry/opentelemetry-demo/src/product-catalog/genproto/oteldemo"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestParseProductRow(t *testing.T) {
	for _, tc := range []struct {
		name       string
		categories string
		want       []string
	}{
		{"empty", "", nil},
		{"one category", "astronomy", []string{"astronomy"}},
		{"trim whitespace", " telescopes, accessories \t", []string{"telescopes", "accessories"}},
		{"preserve ordering", "c,b,a", []string{"c", "b", "a"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := parseProductRow("scope-1", "Telescope", "A portable telescope", "/scope.jpg", "USD", tc.categories, 129, 990000000)
			if got.Id != "scope-1" || got.Name != "Telescope" || got.Description != "A portable telescope" || got.Picture != "/scope.jpg" {
				t.Fatalf("product fields lost: %+v", got)
			}
			if got.PriceUsd == nil || got.PriceUsd.CurrencyCode != "USD" || got.PriceUsd.Units != 129 || got.PriceUsd.Nanos != 990000000 {
				t.Fatalf("price precision/currency lost: %+v", got.PriceUsd)
			}
			if !reflect.DeepEqual(got.Categories, tc.want) {
				t.Errorf("categories = %v, want %v", got.Categories, tc.want)
			}
		})
	}
}

func TestInitDatabaseRequiresConnectionString(t *testing.T) {
	t.Setenv("DB_CONNECTION_STRING", "")
	if err := initDatabase(); err == nil || !strings.Contains(err.Error(), "DB_CONNECTION_STRING") {
		t.Fatalf("expected missing configuration error, got %v", err)
	}
}

func TestMustMapEnv(t *testing.T) {
	t.Setenv("PRODUCT_CATALOG_PORT", "3550")
	var port string
	mustMapEnv(&port, "PRODUCT_CATALOG_PORT")
	if port != "3550" {
		t.Fatalf("port = %q, want 3550", port)
	}
}

func TestDatabaseFunctionsRejectUninitializedConnection(t *testing.T) {
	previous := db
	db = nil
	t.Cleanup(func() { db = previous })
	ctx := context.Background()
	for name, query := range map[string]func() error{
		"list":   func() error { _, err := loadProductsFromDB(ctx); return err },
		"search": func() error { _, err := searchProductsFromDB(ctx, "telescope"); return err },
		"get":    func() error { _, err := getProductFromDB(ctx, "scope-1"); return err },
	} {
		t.Run(name, func(t *testing.T) {
			if err := query(); err == nil || err.Error() != "database connection not initialized" {
				t.Fatalf("expected uninitialized database error, got %v", err)
			}
		})
	}
}

func TestHandlersTranslateDatabaseErrors(t *testing.T) {
	previous := db
	db = nil
	t.Cleanup(func() { db = previous })
	service := &productCatalog{}
	ctx := context.Background()
	t.Run("list", func(t *testing.T) {
		response, err := service.ListProducts(ctx, &pb.Empty{})
		if response != nil || status.Code(err) != codes.Internal {
			t.Fatalf("ListProducts = %v, %v; want nil, Internal", response, err)
		}
	})
	t.Run("search", func(t *testing.T) {
		response, err := service.SearchProducts(ctx, &pb.SearchProductsRequest{Query: "scope"})
		if response != nil || status.Code(err) != codes.Internal {
			t.Fatalf("SearchProducts = %v, %v; want nil, Internal", response, err)
		}
	})
}
