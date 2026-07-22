using SampleService.Models;
using SampleService.Services;

if (string.Equals(Environment.GetEnvironmentVariable("FAIL_STARTUP"), "true", StringComparison.OrdinalIgnoreCase))
{
    throw new InvalidOperationException("Startup failed because FAIL_STARTUP=true.");
}

var builder = WebApplication.CreateBuilder(args);
var allowedOrigins = (Environment.GetEnvironmentVariable("CORS_ALLOW_ORIGINS") ?? "")
    .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

// Ensure the eShop frontend origin is always included in the allowed list
if (allowedOrigins.Length > 0 && !allowedOrigins.Contains("http://eshop.192.168.1.222.nip.io"))
{
    allowedOrigins = allowedOrigins.Append("http://eshop.192.168.1.222.nip.io").ToArray();
}

builder.Services.AddCors(options => options.AddDefaultPolicy(policy =>
{
    if (allowedOrigins.Length == 0)
    {
        policy.AllowAnyOrigin();
    }
    else
    {
        policy.WithOrigins(allowedOrigins);
    }

    policy.AllowAnyHeader().AllowAnyMethod();
}));
builder.Services.AddSingleton<ShopStore>();

var app = builder.Build();
app.UseCors();

app.MapGet("/", () => Results.Ok(new { service = "sample-service", application = "eshop", status = "healthy" }));
app.MapGet("/health", () => Results.Ok(new { status = "healthy", time = DateTimeOffset.UtcNow }));
app.MapGet("/api/health", () => Results.Ok(new { status = "ok", time = DateTimeOffset.UtcNow }));
app.MapGet("/metrics", (ShopStore store) => Results.Text(
    $"# HELP sample_service_up Whether sample-service is running.\n" +
    $"# TYPE sample_service_up gauge\n" +
    $"sample_service_up 1\n" +
    $"# HELP sample_service_products_total Number of products in the catalog.\n" +
    $"# TYPE sample_service_products_total gauge\n" +
    $"sample_service_products_total {store.ProductCount}\n",
    "text/plain; version=0.0.4; charset=utf-8"));

app.MapGet("/api/products", (ShopStore store, string? type) => Results.Ok(store.GetProducts(type)));
app.MapGet("/api/categories", (ShopStore store) => Results.Ok(
    store.GetProducts(null)
        .Select(product => product.Type)
        .Where(type => !string.IsNullOrWhiteSpace(type))
        .Distinct(StringComparer.OrdinalIgnoreCase)
        .OrderBy(type => type)));
app.MapGet("/api/products/{id:int}", (int id, ShopStore store) =>
    store.GetProduct(id) is { } product ? Results.Ok(product) : Results.NotFound());
app.MapGet("/api/cart", (string? userId, ShopStore store) => Results.Ok(store.GetCart(userId ?? "guest")));
app.MapPost("/api/cart", (AddToCartRequest request, ShopStore store) =>
{
    try
    {
        return Results.Ok(store.AddToCart(request.UserId, request.ProductId, request.Quantity));
    }
    catch (ArgumentOutOfRangeException exception)
    {
        return Results.BadRequest(new { error = exception.Message });
    }
});
app.MapDelete("/api/cart/{userId}/items/{productId:int}", (string userId, int productId, ShopStore store) =>
    store.RemoveFromCart(userId, productId) ? Results.NoContent() : Results.NotFound());
app.MapDelete("/api/cart/{userId}", (string userId, ShopStore store) =>
{
    store.ClearCart(userId);
    return Results.NoContent();
});
app.MapGet("/api/orders/{userId}", (string userId, ShopStore store) => Results.Ok(store.GetOrders(userId)));
app.MapPost("/api/orders", (CreateOrderRequest request, ShopStore store) =>
{
    try
    {
        return Results.Ok(store.CreateOrder(request.UserId));
    }
    catch (InvalidOperationException exception)
    {
        return Results.Conflict(new { error = exception.Message });
    }
});

app.MapGet("/openapi.json", () => Results.Ok(new
{
    openapi = "3.0.3",
    info = new { title = "Sample Service eShop API", version = "1.0.0" },
    paths = new Dictionary<string, object>
    {
        ["/api/health"] = new { get = new { summary = "API health" } },
        ["/api/categories"] = new { get = new { summary = "List product categories" } },
        ["/api/products"] = new { get = new { summary = "List products" } },
        ["/api/products/{id}"] = new { get = new { summary = "Get product" } },
        ["/api/cart"] = new { get = new { summary = "Get cart" }, post = new { summary = "Add product to cart" } },
        ["/api/orders"] = new { post = new { summary = "Create order" } },
        ["/api/orders/{userId}"] = new { get = new { summary = "List orders" } }
    }
}));

app.Run();
