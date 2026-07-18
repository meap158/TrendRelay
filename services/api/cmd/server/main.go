package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"github.com/trendrelay/trendrelay/services/api/internal/platform"
)

func main() {
	port := os.Getenv("API_PORT")
	if port == "" { port = "8080" }
	server := &http.Server{Addr: ":" + port, Handler: platform.Routes(), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}
	log.Printf("TrendRelay API listening on %s", server.Addr)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed { log.Fatal(err) }
}
