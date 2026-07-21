using System.Text.Json;
using SampleService.Models;

namespace SampleService.Services;

public sealed class ShopStore
{
    private readonly IReadOnlyList<Product> _products;
    private readonly Dictionary<string, Dictionary<int, int>> _carts = new();
    private readonly List<OrderDto> _orders = [];
    private readonly object _lock = new();

    public ShopStore(IWebHostEnvironment environment)
    {
        var seedPath = Path.Combine(environment.ContentRootPath, "Data", "products.seed.json");
        var products = File.Exists(seedPath)
            ? JsonSerializer.Deserialize<List<Product>>(File.ReadAllText(seedPath))
            : null;
        _products = products?.Count > 0 ? products : [];
    }

    public IReadOnlyList<Product> GetProducts(string? type) => string.IsNullOrWhiteSpace(type)
        ? _products
        : _products.Where(product => string.Equals(product.Type, type.Trim(), StringComparison.OrdinalIgnoreCase)).ToList();

    public Product? GetProduct(int id) => _products.FirstOrDefault(product => product.Id == id);

    public CartDto GetCart(string userId)
    {
        var normalizedUserId = NormalizeUserId(userId);
        lock (_lock)
        {
            if (!_carts.TryGetValue(normalizedUserId, out var items) || items.Count == 0)
            {
                return new CartDto(normalizedUserId, [], 0m);
            }

            var cartItems = items.Select(item => new CartItem(GetProductOrThrow(item.Key), item.Value)).ToList();
            return new CartDto(normalizedUserId, cartItems, cartItems.Sum(item => item.SubTotal));
        }
    }

    public CartDto AddToCart(string userId, int productId, int quantity)
    {
        if (quantity <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(quantity), "Quantity must be greater than 0.");
        }

        _ = GetProductOrThrow(productId);
        var normalizedUserId = NormalizeUserId(userId);
        lock (_lock)
        {
            if (!_carts.TryGetValue(normalizedUserId, out var cart))
            {
                cart = [];
                _carts[normalizedUserId] = cart;
            }

            cart[productId] = cart.GetValueOrDefault(productId) + quantity;
            return GetCart(normalizedUserId);
        }
    }

    public bool RemoveFromCart(string userId, int productId)
    {
        var normalizedUserId = NormalizeUserId(userId);
        lock (_lock)
        {
            if (!_carts.TryGetValue(normalizedUserId, out var cart) || !cart.Remove(productId))
            {
                return false;
            }

            if (cart.Count == 0)
            {
                _carts.Remove(normalizedUserId);
            }

            return true;
        }
    }

    public void ClearCart(string userId)
    {
        lock (_lock)
        {
            _carts.Remove(NormalizeUserId(userId));
        }
    }

    public IReadOnlyList<OrderDto> GetOrders(string userId)
    {
        var normalizedUserId = NormalizeUserId(userId);
        lock (_lock)
        {
            return _orders
                .Where(order => string.Equals(order.UserId, normalizedUserId, StringComparison.OrdinalIgnoreCase))
                .OrderByDescending(order => order.CreatedAt)
                .ToList();
        }
    }

    public OrderDto CreateOrder(string userId)
    {
        var normalizedUserId = NormalizeUserId(userId);
        var cart = GetCart(normalizedUserId);
        if (cart.Items.Count == 0)
        {
            throw new InvalidOperationException("Cart is empty.");
        }

        var items = cart.Items
            .Select(item => new OrderItem(item.Product.Id, item.Product.Name, item.Product.Price, item.Quantity))
            .ToList();
        var order = new OrderDto(
            $"ORD-{DateTime.UtcNow:yyyyMMddHHmmssfff}-{Random.Shared.Next(1000, 9999)}",
            normalizedUserId,
            DateTimeOffset.UtcNow,
            items,
            items.Sum(item => item.SubTotal));

        lock (_lock)
        {
            _orders.Add(order);
            _carts.Remove(normalizedUserId);
        }

        return order;
    }

    public int ProductCount => _products.Count;

    private static string NormalizeUserId(string? userId) =>
        string.IsNullOrWhiteSpace(userId) ? "guest" : userId.Trim().ToLowerInvariant();

    private Product GetProductOrThrow(int productId) =>
        GetProduct(productId) ?? throw new ArgumentOutOfRangeException(nameof(productId), "Product not found.");
}
